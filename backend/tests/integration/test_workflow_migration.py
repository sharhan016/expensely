import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is required")
BEFORE = "a7d3c5e1f904"  # both new revisions (period difference, workflow tables) sit after this


def _alembic(*arguments: str) -> None:
    backend = Path(__file__).resolve().parents[2]
    subprocess.run(
        [str(Path(sys.executable).with_name("alembic")), *arguments],
        cwd=backend,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL or ""},
        check=True,
        capture_output=True,
        text=True,
    )


def _state():
    engine = create_engine(TEST_DATABASE_URL)
    names = set(inspect(engine).get_table_names())
    columns = {c["name"] for c in inspect(engine).get_columns("reconciliations")}
    engine.dispose()
    return names, columns


def test_period_difference_and_workflow_tables_migrate_up_down_up():
    _alembic("upgrade", "head")
    names, columns = _state()
    assert {"review_periods", "acknowledged_gaps", "workflow_runs", "workflow_events"} <= names
    assert "period_difference" in columns

    _alembic("downgrade", BEFORE)
    names, columns = _state()
    assert not {"review_periods", "acknowledged_gaps", "workflow_runs", "workflow_events"} & names
    assert "period_difference" not in columns
    assert "reconciliations" in names  # earlier data tables untouched

    _alembic("upgrade", "head")
    names, columns = _state()
    assert "workflow_runs" in names and "period_difference" in columns
