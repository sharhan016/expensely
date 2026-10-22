"""Spike: does LangGraph meet our exit criteria? Runs real separate processes against PostgreSQL.

Criteria: (1) start, interrupt, restart the process, resume without repeating a side effect;
(2) checkpoint tables are created by PostgresSaver.setup(), outside Alembic (documented in
docs/workflow-checkpoints.md); (3) a scan of every checkpoint table finds no sensitive text,
with a positive control proving the scan can see text when it is there.
"""

import contextlib
import os
import subprocess
import sys
import uuid

import psycopg
import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is required")

SECRETS = (
    "ZEBRAQUOKKA PAYEE",
    "Priyanka Synthetic-Holder",
    "5550123456789",
    "4321.55",
    "hunter2-pw",
)

SCRIPT = r"""
import os, sys
from typing import TypedDict
import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

URL = os.environ["SPIKE_URL"]
mode, run_id = sys.argv[1], sys.argv[2]
leak = os.environ.get("SPIKE_LEAK", "")

class State(TypedDict):
    run_id: str
    stage: str
    note: str

def effect(name):
    with psycopg.connect(URL, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO spike_effects (run_id, name) VALUES (%s, %s)", (run_id, name)
        )

def before(state):
    effect("before")                    # runs once: a finished node is never re-run on resume
    return {"stage": "before", "note": leak}

def ask(state):
    answer = interrupt({"type": "period"})   # pauses here; the value is persisted in the checkpoint
    effect("after_answer")              # side effect placed after the interrupt runs once
    return {"stage": "answered:" + str(answer["code"])}

builder = StateGraph(State)
builder.add_node("before", before)
builder.add_node("ask", ask)
builder.add_edge(START, "before")
builder.add_edge("before", "ask")
builder.add_edge("ask", END)

with PostgresSaver.from_conn_string(URL) as saver:
    saver.setup()
    graph = builder.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": run_id}}
    if mode == "start":
        result = graph.invoke({"run_id": run_id, "stage": "new", "note": ""}, config)
        print("INTERRUPTED" if "__interrupt__" in result else "DONE")
    else:
        result = graph.invoke(Command(resume={"code": sys.argv[3]}), config)
        print("DONE:" + result["stage"])
"""


def _run(url, mode, run_id, *extra, leak=""):
    environment = {**os.environ, "SPIKE_URL": url, "SPIKE_LEAK": leak}
    completed = subprocess.run(
        [sys.executable, "-c", SCRIPT, mode, run_id, *extra],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def _checkpoint_text(url: str, run_id: str) -> str:
    chunks = []
    with psycopg.connect(url) as connection:
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            rows = connection.execute(
                f"SELECT t::text FROM {table} t WHERE thread_id = %s", (run_id,)
            ).fetchall()
            chunks.extend(str(row[0]) for row in rows)
            blobs = (
                connection.execute(
                    f"SELECT convert_from(blob, 'LATIN1') FROM {table} WHERE thread_id = %s "
                    "AND blob IS NOT NULL",
                    (run_id,),
                ).fetchall()
                if table == "checkpoint_blobs"
                else []
            )
            chunks.extend(str(row[0]) for row in blobs)
    return "\n".join(chunks)


@pytest.fixture
def url():
    libpq = _libpq(TEST_DATABASE_URL)
    with psycopg.connect(libpq, autocommit=True) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS spike_effects "
            "(id serial PRIMARY KEY, run_id text, name text)"
        )
    return libpq


def _effects(url, run_id):
    with psycopg.connect(url) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT name FROM spike_effects WHERE run_id = %s ORDER BY id", (run_id,)
            )
        ]


def test_start_interrupt_restart_and_resume_without_repeating_side_effects(url):
    run_id = f"spike-{uuid.uuid4().hex}"

    assert _run(url, "start", run_id) == "INTERRUPTED"
    assert _effects(url, run_id) == ["before"]

    # A brand new process (the "restart") resumes from the stored checkpoint alone.
    assert _run(url, "resume", run_id, "provided") == "DONE:answered:provided"
    assert _effects(url, run_id) == ["before", "after_answer"]

    # Resuming again must not repeat anything (idempotent replay).
    with contextlib.suppress(subprocess.CalledProcessError):
        _run(url, "resume", run_id, "provided")
    assert _effects(url, run_id) == ["before", "after_answer"]


def test_checkpoint_tables_exist_outside_alembic_and_hold_no_sensitive_text(url):
    run_id = f"spike-{uuid.uuid4().hex}"
    _run(url, "start", run_id)
    _run(url, "resume", run_id, "not_needed")

    with psycopg.connect(url) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        alembic_versions = {
            row[0] for row in connection.execute("SELECT version_num FROM alembic_version")
        }
    assert {
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
    } <= tables
    assert alembic_versions  # Alembic is unaware of the checkpoint tables
    text = _checkpoint_text(url, run_id)
    assert run_id in text and "not_needed" in text  # the scan really reads checkpoint content
    for secret in SECRETS:
        assert secret not in text


def test_positive_control_the_scan_finds_text_that_is_in_state(url):
    run_id = f"spike-{uuid.uuid4().hex}"
    _run(url, "start", run_id, leak="ZEBRAQUOKKA PAYEE")

    assert "ZEBRAQUOKKA PAYEE" in _checkpoint_text(url, run_id)
