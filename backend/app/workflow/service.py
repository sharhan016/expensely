"""Workflow runs: our tables are the source of truth; LangGraph only pauses and resumes.

Graph state, interrupt payloads and resume values hold ids, dates and codes only. A password is
used inside the request that receives it and the graph is resumed with a code.
"""

import contextlib
import uuid
from datetime import date, timedelta
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AcknowledgedGap,
    ImportStatus,
    ReviewPeriod,
    StatementImport,
    WorkflowEvent,
    WorkflowRun,
)

LIMITS_MESSAGE = (
    "What I can and cannot tell you: I can read the statements you give me, check that they add "
    "up, and say which dates they cover. I cannot tell what happened on dates with no statement, "
    "I will not guess a balance, and cash I can only report as withdrawn, not as a balance."
)
DAY = timedelta(days=1)
_memory_saver = InMemorySaver()
_prepared_urls: set[str] = set()


class State(TypedDict):
    run_id: str
    user_id: int
    import_ids: list[str]
    answered_gaps: list[str]
    stage: str


# ---- events ----------------------------------------------------------------------------------


def emit(db: Session, run_id, user_id: int, stage: str, message: str, key: str, status="info"):
    """Append a plain-language event once per (run, key); replays add nothing."""
    run_id = uuid.UUID(str(run_id))  # graph state carries the id as text
    if db.scalar(
        select(WorkflowEvent.id).where(
            WorkflowEvent.run_id == run_id, WorkflowEvent.step_key == key
        )
    ):
        return
    seq = db.scalar(select(func.max(WorkflowEvent.seq)).where(WorkflowEvent.run_id == run_id)) or 0
    db.add(
        WorkflowEvent(
            run_id=run_id,
            user_id=user_id,
            seq=seq + 1,
            stage=stage,
            message=message,
            machine_status=status,
            step_key=key,
        )
    )
    db.flush()


# ---- gap detection -----------------------------------------------------------------------------


def statement_gap(db: Session, statement: StatementImport) -> dict | None:
    """The unreported span between this staged statement and what is already confirmed."""
    if statement.status != ImportStatus.READY or not statement.account_id:
        return None
    if not statement.period_start:
        return None
    prior = db.scalar(
        select(StatementImport)
        .where(
            StatementImport.user_id == statement.user_id,
            StatementImport.account_id == statement.account_id,
            StatementImport.status == ImportStatus.CONFIRMED,
            StatementImport.period_end.is_not(None),
            StatementImport.period_end < statement.period_start,
        )
        .order_by(StatementImport.period_end.desc())
        .limit(1)
    )
    if prior is None:
        return None
    gap_start, gap_end = prior.period_end + DAY, statement.period_start - DAY
    if gap_start > gap_end:
        return None
    bridged = (
        prior.closing_balance is not None
        and statement.opening_balance is not None
        and prior.closing_balance == statement.opening_balance
    )
    return {
        "import_id": str(statement.id),
        "account_id": statement.account_id,
        "gap_start": gap_start,
        "gap_end": gap_end,
        "bridged": bridged,
    }


def _gap_key(gap: dict) -> str:
    return f"{gap['account_id']}:{gap['gap_start'].isoformat()}:{gap['gap_end'].isoformat()}"


def _open_gaps(db: Session, state: State) -> list[dict]:
    acknowledged = {
        (item.account_id, item.gap_start, item.gap_end)
        for item in db.scalars(
            select(AcknowledgedGap).where(AcknowledgedGap.user_id == state["user_id"])
        )
    }
    gaps = []
    for import_id in state["import_ids"]:
        statement = db.scalar(
            select(StatementImport).where(
                StatementImport.id == uuid.UUID(import_id),
                StatementImport.user_id == state["user_id"],
            )
        )
        gap = statement_gap(db, statement) if statement else None
        if (
            gap
            and not gap["bridged"]
            and _gap_key(gap) not in state["answered_gaps"]
            and (gap["account_id"], gap["gap_start"], gap["gap_end"]) not in acknowledged
        ):
            gaps.append(gap)
    return gaps


# ---- graph -------------------------------------------------------------------------------------


def _locked(db: Session, state: State) -> list[StatementImport]:
    rows = []
    for import_id in state["import_ids"]:
        statement = db.scalar(
            select(StatementImport).where(
                StatementImport.id == uuid.UUID(import_id),
                StatementImport.user_id == state["user_id"],
            )
        )
        if statement and statement.status == ImportStatus.PASSWORD_REQUIRED:
            rows.append(statement)
    return rows


def build_graph(db: Session, saver):
    def unlock(state: State):
        run_id, user_id = state["run_id"], state["user_id"]
        for index, import_id in enumerate(state["import_ids"], start=1):
            statement = db.scalar(
                select(StatementImport).where(
                    StatementImport.id == uuid.UUID(import_id), StatementImport.user_id == user_id
                )
            )
            label = f"statement {index}"
            if statement.status == ImportStatus.READY:
                emit(
                    db,
                    run_id,
                    user_id,
                    "read",
                    f"Read {label}: {len(statement.rows)} rows found.",
                    f"read:{import_id}",
                )
            elif statement.status == ImportStatus.FAILED:
                emit(
                    db,
                    run_id,
                    user_id,
                    "read",
                    f"I could not read {label}. Nothing was added.",
                    f"failed:{import_id}",
                    "warning",
                )
        locked = _locked(db, state)
        if locked:
            target = locked[0]
            emit(
                db,
                run_id,
                user_id,
                "unlock",
                "A statement is password protected. I need its "
                "password to read it; I use it once and never store it.",
                f"locked:{target.id}",
                "waiting",
            )
            interrupt({"type": "password", "import_id": str(target.id)})
            db.refresh(target)
            emit(
                db,
                run_id,
                user_id,
                "unlock",
                "Unlocked a protected statement and read it.",
                f"unlocked:{target.id}",
            )
        return {"stage": "unlock"}

    def period(state: State):
        run_id, user_id = state["run_id"], state["user_id"]
        if not db.scalar(select(ReviewPeriod.id).where(ReviewPeriod.user_id == user_id)):
            emit(
                db,
                run_id,
                user_id,
                "period",
                "I need to know which period you want to understand.",
                "period:ask",
                "waiting",
            )
            interrupt({"type": "period"})
            emit(db, run_id, user_id, "period", "Saved your review period.", "period:saved")
        else:
            emit(db, run_id, user_id, "period", "Using your saved review period.", "period:use")
        return {"stage": "period"}

    def gaps(state: State):
        run_id, user_id = state["run_id"], state["user_id"]
        for import_id in state["import_ids"]:
            statement = db.scalar(
                select(StatementImport).where(
                    StatementImport.id == uuid.UUID(import_id), StatementImport.user_id == user_id
                )
            )
            gap = statement_gap(db, statement) if statement else None
            if gap and gap["bridged"]:
                emit(
                    db,
                    run_id,
                    user_id,
                    "gaps",
                    "Some dates have no statement rows, but the "
                    "balances connect exactly, so I bridged them and will show that.",
                    f"bridged:{_gap_key(gap)}",
                )
        open_gaps = _open_gaps(db, state)
        if not open_gaps:
            # A resumed run lands here once her answer made the gap acknowledged: close each
            # question that was asked. Replays add nothing (unique step key).
            asked = db.scalars(
                select(WorkflowEvent.step_key).where(
                    WorkflowEvent.run_id == uuid.UUID(run_id),
                    WorkflowEvent.step_key.like("gap:ask:%"),
                )
            ).all()
            for key in asked:
                emit(
                    db,
                    run_id,
                    user_id,
                    "gaps",
                    "Noted your answer about those dates.",
                    "gap:done:" + key.removeprefix("gap:ask:"),
                )
            return {"stage": "gaps"}
        gap = open_gaps[0]
        key = _gap_key(gap)
        emit(
            db,
            run_id,
            user_id,
            "gaps",
            "There is a stretch of dates between your last "
            "reconciled statement and this one that nothing covers.",
            f"gap:ask:{key}",
            "waiting",
        )
        interrupt(
            {
                "type": "gap",
                "account_id": gap["account_id"],
                "gap_start": gap["gap_start"].isoformat(),
                "gap_end": gap["gap_end"].isoformat(),
                "import_id": gap["import_id"],
            }
        )
        emit(db, run_id, user_id, "gaps", "Noted your answer about those dates.", f"gap:done:{key}")
        return {"stage": "gaps", "answered_gaps": [*state["answered_gaps"], key]}

    def finish(state: State):
        run_id, user_id = state["run_id"], state["user_id"]
        emit(db, run_id, user_id, "limits", LIMITS_MESSAGE, "limits")
        if state["import_ids"]:
            emit(
                db,
                run_id,
                user_id,
                "done",
                "Your statements are ready for you to review before "
                "anything is added to the ledger.",
                "done",
            )
        return {"stage": "done"}

    builder = StateGraph(State)
    builder.add_node("unlock", unlock)
    builder.add_node("period", period)
    builder.add_node("gaps", gaps)
    builder.add_node("finish", finish)
    builder.add_edge(START, "unlock")
    builder.add_conditional_edges(
        "unlock", lambda state: "unlock" if _locked(db, state) else "period", ["unlock", "period"]
    )
    builder.add_edge("period", "gaps")
    builder.add_conditional_edges(
        "gaps", lambda state: "gaps" if _open_gaps(db, state) else "finish", ["gaps", "finish"]
    )
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=saver)


@contextlib.contextmanager
def checkpointer(db: Session):
    """PostgreSQL in production and PostgreSQL tests; an in-memory saver on SQLite unit tests."""
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        yield _memory_saver
        return
    url = bind.engine.url.render_as_string(hide_password=False).replace("+psycopg", "")
    with PostgresSaver.from_conn_string(url) as saver:
        if url not in _prepared_urls:
            saver.setup()  # creates the checkpoint tables outside Alembic; idempotent
            _prepared_urls.add(url)
        yield saver


# ---- running ---------------------------------------------------------------------------------


def _advance(db: Session, run: WorkflowRun, graph_input) -> None:
    config = {"configurable": {"thread_id": str(run.id)}}
    try:
        with checkpointer(db) as saver:
            result = build_graph(db, saver).invoke(graph_input, config)
    except Exception:
        db.rollback()
        run = db.get(WorkflowRun, run.id)
        run.status, run.waiting_for, run.current_stage = "failed", None, "failed"
        emit(
            db,
            run.id,
            run.user_id,
            "failed",
            "Something went wrong. Nothing was changed.",
            "failed",
            "error",
        )
        db.commit()
        raise
    pending = result.get("__interrupt__")
    if pending:
        run.status = "waiting"
        run.waiting_for = dict(pending[0].value)
        run.current_stage = run.waiting_for["type"]
    else:
        run.status, run.waiting_for, run.current_stage = "completed", None, "done"
    db.commit()


def start_run(
    db: Session, user_id: int, import_ids: list[str], client_key: str | None
) -> WorkflowRun:
    if client_key:
        existing = db.scalar(
            select(WorkflowRun).where(
                WorkflowRun.user_id == user_id, WorkflowRun.client_key == client_key
            )
        )
        if existing:
            return existing
    run = WorkflowRun(
        user_id=user_id,
        kind="intake" if import_ids else "welcome",
        status="running",
        import_ids=import_ids,
        client_key=client_key,
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        # A concurrent request with the same client key won the race: return its run.
        db.rollback()
        return db.scalar(
            select(WorkflowRun).where(
                WorkflowRun.user_id == user_id, WorkflowRun.client_key == client_key
            )
        )
    emit(db, run.id, user_id, "start", "Started looking at what you gave me.", "start")
    db.commit()
    _advance(
        db,
        run,
        {
            "run_id": str(run.id),
            "user_id": user_id,
            "import_ids": import_ids,
            "answered_gaps": [],
            "stage": "start",
        },
    )
    return run


def resume_run(db: Session, run: WorkflowRun, code: dict) -> None:
    _advance(db, run, Command(resume=code))


def question_text(db: Session, run: WorkflowRun) -> str | None:
    waiting = run.waiting_for
    if not waiting:
        return None
    if waiting["type"] == "period":
        return (
            "Which period do you want to understand? Tell me the first and last date, then "
            "send the statements that cover it."
        )
    if waiting["type"] == "password":
        return (
            "This PDF is password protected. Enter its password to continue. I use it once "
            "to read the file and never store it."
        )
    if waiting["type"] == "gap":
        start = date.fromisoformat(waiting["gap_start"])
        end = date.fromisoformat(waiting["gap_end"])
        account = db.get(Account, waiting["account_id"])
        from app.coverage import date_label

        return (
            f"Nothing covers {date_label(start)} to {date_label(end)} on {account.name}, and the "
            "balances on either side do not connect. Do you have the statement for those dates, "
            "or was that period not needed?"
        )
    return None


def run_view(db: Session, run: WorkflowRun) -> dict:
    events = db.scalars(
        select(WorkflowEvent).where(WorkflowEvent.run_id == run.id).order_by(WorkflowEvent.seq)
    ).all()
    return {
        "id": str(run.id),
        "kind": run.kind,
        "status": run.status,
        "current_stage": run.current_stage,
        "waiting_for": run.waiting_for,
        "question": question_text(db, run),
        "events": [
            {
                "seq": item.seq,
                "stage": item.stage,
                "message": item.message,
                "status": item.machine_status,
                "created_at": item.created_at.isoformat(),
            }
            for item in events
        ],
    }
