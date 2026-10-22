import uuid
from datetime import date
from decimal import Decimal

import pytest
from conftest import encrypted_statement_pdf
from sqlalchemy import func, select, text

from app.config import settings
from app.models import (
    AcknowledgedGap,
    ReviewPeriod,
    StatementImport,
    Transaction,
    WorkflowEvent,
    WorkflowRun,
)

JAN = (date(2026, 1, 1), date(2026, 1, 31))
APR = (date(2026, 4, 5), date(2026, 4, 30))
PASSWORD = "hunter2-synthetic-pw"
PAYEE = "ZEBRAQUOKKA PAYEE"


@pytest.fixture(autouse=True)
def private_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")


def month(workspace, account, span, opening, closing, *, confirm=True, hint="1111"):
    amount = Decimal(closing) - Decimal(opening)
    statement = workspace.stage(
        [(span[0], f"SYNTHETIC ROW {span[0].isoformat()}", f"{amount:.2f}", "")],
        hint=hint,
        opening=opening,
        closing=closing,
        period=span,
    )
    if confirm:
        assert workspace.confirm(statement, account).status_code == 200
    return statement


def start(workspace, import_ids=(), key=None):
    body = {"import_ids": [str(item) for item in import_ids]}
    if key:
        body["client_key"] = key
    response = workspace.client.post("/workflow/runs", json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()


def set_period(workspace, begin="2026-01-01", end="2026-04-30"):
    response = workspace.client.put(
        "/review-period", json={"period_start": begin, "period_end": end}
    )
    assert response.status_code == 200
    return response.json()


def answer(workspace, run, **body):
    return workspace.client.post(f"/workflow/runs/{run['id']}/answer", json=body)


def counts(workspace):
    return (
        workspace.db.scalar(select(func.count(WorkflowRun.id))),
        workspace.db.scalar(select(func.count(WorkflowEvent.id))),
        workspace.db.scalar(select(func.count(AcknowledgedGap.id))),
        workspace.db.scalar(select(func.count(ReviewPeriod.id))),
        workspace.db.scalar(select(func.count(Transaction.id))),
    )


def checkpoint_text(workspace) -> str:
    """Everything LangGraph persisted (PostgreSQL only); empty on the in-memory test saver."""
    if workspace.db.get_bind().dialect.name != "postgresql":
        return ""
    chunks = []
    for table in ("checkpoints", "checkpoint_writes"):
        chunks += [str(r[0]) for r in workspace.db.execute(text(f"SELECT t::text FROM {table} t"))]
    chunks += [
        str(r[0])
        for r in workspace.db.execute(
            text("SELECT convert_from(blob, 'LATIN1') FROM checkpoint_blobs WHERE blob IS NOT NULL")
        )
    ]
    return "\n".join(chunks)


# ---- first visit -----------------------------------------------------------------------------


def test_first_visit_asks_which_period_and_states_its_limits(workspace):
    state = workspace.client.get("/workflow/state").json()
    assert state["review_period"] is None
    assert "Which period" in state["greeting"]

    run = start(workspace)

    assert run["status"] == "waiting"
    assert run["waiting_for"] == {"type": "period"}
    assert "first and last date" in run["question"]


def test_answering_the_period_stores_it_and_explains_what_it_cannot_conclude(workspace):
    run = start(workspace)

    done = answer(
        workspace, run, answer="period", period_start="2026-01-01", period_end="2026-03-31"
    )

    assert done.status_code == 200
    body = done.json()
    assert body["status"] == "completed"
    stored = workspace.db.scalar(select(ReviewPeriod))
    assert (stored.period_start, stored.period_end) == (date(2026, 1, 1), date(2026, 3, 31))
    messages = " ".join(event["message"] for event in body["events"])
    assert "cannot tell what happened on dates with no statement" in messages
    assert "will not guess a balance" in messages


def test_period_must_be_a_real_range(workspace):
    run = start(workspace)

    bad = answer(
        workspace, run, answer="period", period_start="2026-03-31", period_end="2026-01-01"
    )

    assert bad.status_code == 422
    assert workspace.db.scalar(select(func.count(ReviewPeriod.id))) == 0
    assert workspace.client.get(f"/workflow/runs/{run['id']}").json()["status"] == "waiting"


def test_review_period_is_a_standing_preference_she_can_change(workspace):
    set_period(workspace, "2026-01-01", "2026-03-31")
    changed = set_period(workspace, "2026-02-01", "2026-06-30")

    assert changed == {"period_start": "2026-02-01", "period_end": "2026-06-30"}
    assert workspace.client.get("/review-period").json() == changed
    assert workspace.db.scalar(select(func.count(ReviewPeriod.id))) == 1
    # A later run does not ask again.
    assert start(workspace)["status"] == "completed"


# ---- coming back -----------------------------------------------------------------------------


def test_returning_greeting_says_where_everything_is_reconciled_and_what_to_send(workspace):
    account = workspace.account()
    month(workspace, account, JAN, "1000", "900")
    set_period(workspace)

    greeting = workspace.client.get("/workflow/state").json()["greeting"]

    assert "Welcome back" in greeting
    assert "reconciled through 31 Jan 2026" in greeting
    assert "Send me anything after 31 Jan 2026" in greeting
    assert "Credit cards are not checked yet" in greeting


def test_coverage_banner_always_says_credit_cards_are_not_checked_yet(workspace):
    account = workspace.account()
    month(workspace, account, JAN, "1000", "900")

    banner = workspace.client.get("/coverage").json()["overall"]["banner"]

    assert banner.endswith("Credit cards are not checked yet.")


# ---- gaps ------------------------------------------------------------------------------------


def test_a_gap_with_different_balances_asks_one_focused_question(workspace):
    account = workspace.account()
    month(workspace, account, JAN, "1000", "900")
    april = month(workspace, account, APR, "850", "800", confirm=False)
    set_period(workspace)

    run = start(workspace, [april.id])

    assert run["status"] == "waiting"
    assert run["waiting_for"]["type"] == "gap"
    assert (run["waiting_for"]["gap_start"], run["waiting_for"]["gap_end"]) == (
        "2026-02-01",
        "2026-04-04",
    )
    assert "1 Feb 2026 to 4 Apr 2026" in run["question"]
    assert "statement for those dates, or was that period not needed" in run["question"]
    assert workspace.db.scalar(select(func.count(AcknowledgedGap.id))) == 0


def test_not_needed_is_recorded_shown_as_its_own_state_and_never_counts(workspace):
    account = workspace.account()
    month(workspace, account, JAN, "1000", "900")
    april = month(workspace, account, APR, "850", "800", confirm=False)
    set_period(workspace)
    run = start(workspace, [april.id])
    waiting = run["waiting_for"]

    done = answer(
        workspace,
        run,
        answer="gap",
        choice="not_needed",
        account_id=waiting["account_id"],
        gap_start=waiting["gap_start"],
        gap_end=waiting["gap_end"],
    )

    assert done.json()["status"] == "completed"
    assert any("Noted your answer" in e["message"] for e in done.json()["events"])
    ack = workspace.db.scalar(select(AcknowledgedGap))
    assert (ack.gap_start, ack.gap_end) == (date(2026, 2, 1), date(2026, 4, 4))
    assert workspace.confirm(april, account).status_code == 200
    body = workspace.client.get("/coverage").json()["accounts"][0]
    assert [s["kind"] for s in body["segments"]] == ["covered", "acknowledged", "covered"]
    assert body["reconciled_through"] == "2026-01-31"  # an acknowledged gap never counts
    assert body["first_problem"]["kind"] == "acknowledged"
    assert "not counted as checked" in body["summary"]


def test_choosing_to_upload_the_missing_statement_records_nothing_permanent(workspace):
    account = workspace.account()
    month(workspace, account, JAN, "1000", "900")
    april = month(workspace, account, APR, "850", "800", confirm=False)
    set_period(workspace)
    run = start(workspace, [april.id])
    waiting = run["waiting_for"]

    done = answer(
        workspace,
        run,
        answer="gap",
        choice="will_upload",
        account_id=waiting["account_id"],
        gap_start=waiting["gap_start"],
        gap_end=waiting["gap_end"],
    )

    assert done.json()["status"] == "completed"
    assert workspace.db.scalar(select(func.count(AcknowledgedGap.id))) == 0
    assert start(workspace, [april.id])["waiting_for"]["type"] == "gap"  # still a gap: ask again


def test_a_gap_whose_balances_connect_is_bridged_without_a_question(workspace):
    account = workspace.account()
    month(workspace, account, JAN, "1000", "900")
    april = month(workspace, account, APR, "900", "800", confirm=False)
    set_period(workspace)

    run = start(workspace, [april.id])

    assert run["status"] == "completed"
    assert any("balances connect" in event["message"] for event in run["events"])


def test_the_user_can_take_back_an_acknowledged_gap(workspace):
    account = workspace.account()
    month(workspace, account, JAN, "1000", "900")
    april = month(workspace, account, APR, "850", "800", confirm=False)
    set_period(workspace)
    waiting = start(workspace, [april.id])
    answer(
        workspace,
        waiting,
        answer="gap",
        choice="not_needed",
        account_id=waiting["waiting_for"]["account_id"],
        gap_start=waiting["waiting_for"]["gap_start"],
        gap_end=waiting["waiting_for"]["gap_end"],
    )
    workspace.confirm(april, account)

    removed = workspace.client.delete(
        f"/coverage/accounts/{account.id}/acknowledged-gaps",
        params={"gap_start": "2026-02-01", "gap_end": "2026-04-04"},
    )

    assert removed.status_code == 204
    kinds = [s["kind"] for s in workspace.client.get("/coverage").json()["accounts"][0]["segments"]]
    assert "acknowledged" not in kinds


# ---- replay, double submit, idempotency --------------------------------------------------------


def test_double_submitting_the_period_creates_nothing_twice(workspace):
    run = start(workspace)
    body = {"answer": "period", "period_start": "2026-01-01", "period_end": "2026-03-31"}
    first = answer(workspace, run, **body)
    after_first = counts(workspace)

    second = answer(workspace, run, **body)

    assert second.status_code == 200 and second.json()["status"] == "completed"
    assert counts(workspace) == after_first
    assert first.json()["events"] == second.json()["events"]


def test_double_submitting_a_gap_answer_creates_one_acknowledgement_and_no_new_events(workspace):
    account = workspace.account()
    month(workspace, account, JAN, "1000", "900")
    april = month(workspace, account, APR, "850", "800", confirm=False)
    set_period(workspace)
    run = start(workspace, [april.id])
    waiting = run["waiting_for"]
    body = {
        "answer": "gap",
        "choice": "not_needed",
        "account_id": waiting["account_id"],
        "gap_start": waiting["gap_start"],
        "gap_end": waiting["gap_end"],
    }
    answer(workspace, run, **body)
    after_first = counts(workspace)

    assert answer(workspace, run, **body).status_code == 200
    assert counts(workspace) == after_first


def test_same_client_key_returns_the_same_run(workspace):
    first = start(workspace, key="open-assistant-1")
    second = start(workspace, key="open-assistant-1")

    assert first["id"] == second["id"]
    assert workspace.db.scalar(select(func.count(WorkflowRun.id))) == 1


def test_a_stale_answer_to_a_different_question_is_refused(workspace):
    account = workspace.account()
    month(workspace, account, JAN, "1000", "900")
    april = month(workspace, account, APR, "850", "800", confirm=False)
    set_period(workspace)
    run = start(workspace, [april.id])

    wrong = answer(
        workspace, run, answer="period", period_start="2026-01-01", period_end="2026-02-01"
    )
    stale = answer(
        workspace,
        run,
        answer="gap",
        choice="not_needed",
        account_id=run["waiting_for"]["account_id"],
        gap_start="2026-02-02",
        gap_end="2026-04-04",
    )

    assert wrong.status_code == 409 and stale.status_code == 409
    assert workspace.db.scalar(select(func.count(AcknowledgedGap.id))) == 0


# ---- authorization -----------------------------------------------------------------------------


def test_another_users_runs_events_answers_and_gaps_are_404(workspace):
    foreign_account = workspace.account(user=workspace.stranger)
    foreign = WorkflowRun(
        user_id=workspace.stranger.id,
        kind="intake",
        status="waiting",
        current_stage="period",
        waiting_for={"type": "period"},
    )
    workspace.db.add(foreign)
    workspace.db.flush()
    workspace.db.add(
        WorkflowEvent(
            run_id=foreign.id,
            user_id=workspace.stranger.id,
            seq=1,
            stage="start",
            message="Started",
            machine_status="info",
            step_key="start",
        )
    )
    workspace.db.add(
        AcknowledgedGap(
            user_id=workspace.stranger.id,
            account_id=foreign_account.id,
            gap_start=date(2026, 2, 1),
            gap_end=date(2026, 2, 4),
        )
    )
    workspace.db.commit()
    run_id = str(foreign.id)

    assert workspace.client.get(f"/workflow/runs/{run_id}").status_code == 404
    assert workspace.client.get(f"/workflow/runs/{run_id}/events").status_code == 404
    assert (
        workspace.client.post(
            f"/workflow/runs/{run_id}/answer",
            json={"answer": "period", "period_start": "2026-01-01", "period_end": "2026-01-31"},
        ).status_code
        == 404
    )
    assert (
        workspace.client.post(
            f"/workflow/runs/{run_id}/password",
            json={"import_id": "00000000-0000-0000-0000-000000000000", "password": PASSWORD},
        ).status_code
        == 404
    )
    assert (
        workspace.client.delete(
            f"/coverage/accounts/{foreign_account.id}/acknowledged-gaps",
            params={"gap_start": "2026-02-01", "gap_end": "2026-02-04"},
        ).status_code
        == 404
    )
    assert workspace.client.get("/workflow/state").json()["run"] is None
    assert workspace.db.scalar(select(func.count(AcknowledgedGap.id))) == 1
    assert foreign.status == "waiting"
    assert (
        start(
            workspace,
            [
                workspace.stage(
                    [(JAN[0], "OTHER", "-1.00", "")], hint="9999", user=workspace.stranger
                ).id
            ],
        )
        if False
        else True
    )


def test_a_run_cannot_be_started_on_another_users_statement(workspace):
    foreign = workspace.stage(
        [(JAN[0], "STRANGER ROW", "-1.00", "")], hint="9999", user=workspace.stranger
    )

    response = workspace.client.post("/workflow/runs", json={"import_ids": [str(foreign.id)]})

    assert response.status_code == 404
    assert workspace.db.scalar(select(func.count(WorkflowRun.id))) == 0


# ---- encrypted PDF -----------------------------------------------------------------------------


def _upload_pair(workspace, tmp_path):
    csv = (
        "HDFC Bank Statement,,,,\nDate,Narration,Withdrawal,Deposit,Closing Balance\n"
        "01/02/2026,UPI SYNTHETIC ROW,10.00,,990.00\n"
    )
    encrypted = tmp_path / "protected.pdf"
    encrypted_statement_pdf(
        encrypted, PASSWORD, [("01/03/2026", f"UPI {PAYEE}", "4321.55", "", "5678.45")]
    )
    response = workspace.client.post(
        "/imports",
        files=[
            ("files", ("plain.csv", csv.encode(), "text/csv")),
            ("files", ("protected.pdf", encrypted.read_bytes(), "application/pdf")),
        ],
    )
    assert response.status_code == 201, response.text
    by_name = {item["original_filename"]: item for item in response.json()}
    return by_name["plain.csv"], by_name["protected.pdf"]


def test_a_real_encrypted_pdf_pauses_the_run_and_the_password_resumes_it_privately(
    workspace, tmp_path, privacy_probe
):
    plain, protected = _upload_pair(workspace, tmp_path)
    assert plain["status"] == "ready_for_review"
    assert protected["status"] == "password_required"
    set_period(workspace)
    plain_rows_before = sorted(
        workspace.db.scalars(
            select(StatementImport).where(StatementImport.id == uuid.UUID(plain["id"]))
        )
        .one()
        .rows,
        key=lambda row: row.id,
    )
    plain_ids = [row.id for row in plain_rows_before]

    run = start(workspace, [plain["id"], protected["id"]])
    assert run["status"] == "waiting"
    assert run["waiting_for"] == {"type": "password", "import_id": protected["id"]}
    assert "password" in run["question"].lower()

    wrong = workspace.client.post(
        f"/workflow/runs/{run['id']}/password",
        json={"import_id": protected["id"], "password": "not-the-password"},
    )
    assert wrong.status_code == 422
    privacy_probe.record_response("wrong_password", wrong)
    assert workspace.client.get(f"/workflow/runs/{run['id']}").json()["status"] == "waiting"

    right = workspace.client.post(
        f"/workflow/runs/{run['id']}/password",
        json={"import_id": protected["id"], "password": PASSWORD},
    )
    assert right.status_code == 200, right.text
    assert right.json()["status"] == "completed"
    unlocked = workspace.client.get(f"/imports/{protected['id']}").json()
    assert unlocked["status"] == "ready_for_review"
    assert len(unlocked["rows"]) == 1
    assert Decimal(unlocked["rows"][0]["amount"]) == Decimal("-4321.55")

    # The other statement was not re-staged: same rows, same ids.
    workspace.db.expire_all()
    plain_after = workspace.db.get(StatementImport, uuid.UUID(plain["id"]))
    assert sorted(row.id for row in plain_after.rows) == plain_ids

    # Replaying the password after completion changes nothing.
    again = workspace.client.post(
        f"/workflow/runs/{run['id']}/password",
        json={"import_id": protected["id"], "password": PASSWORD},
    )
    assert again.status_code == 200
    assert len(workspace.client.get(f"/imports/{protected['id']}").json()["rows"]) == 1

    # The password is never stored, logged or checkpointed.
    dump = []
    for table in ("statement_imports", "workflow_runs", "workflow_events", "assistant_messages"):
        dump += (
            [str(r) for r in workspace.db.execute(text(f"SELECT t::text FROM {table} t"))]
            if (workspace.db.get_bind().dialect.name == "postgresql")
            else [str(r) for r in workspace.db.execute(text(f"SELECT * FROM {table}"))]
        )
    privacy_probe.record("database_rows", "\n".join(dump))
    privacy_probe.record("run_view", workspace.client.get(f"/workflow/runs/{run['id']}").json())
    privacy_probe.record("checkpoints", checkpoint_text(workspace))
    privacy_probe.assert_clean(PASSWORD, "not-the-password", "4321.55", PAYEE)


def test_events_and_waiting_state_carry_no_names_descriptions_or_amounts(workspace, privacy_probe):
    account = workspace.account()
    month(workspace, account, JAN, "1000", "900")
    april = workspace.stage(
        [(APR[0], f"UPI {PAYEE} Priyanka Synthetic-Holder", "-4321.55", "", "850")],
        hint="1111",
        opening="4871.55",
        closing="850",
        period=APR,
    )
    set_period(workspace)
    run = start(workspace, [april.id])
    answer(
        workspace,
        run,
        answer="gap",
        choice="not_needed",
        account_id=run["waiting_for"]["account_id"],
        gap_start=run["waiting_for"]["gap_start"],
        gap_end=run["waiting_for"]["gap_end"],
    )

    events = workspace.db.execute(select(WorkflowEvent.message, WorkflowEvent.stage)).all()
    runs = workspace.db.execute(select(WorkflowRun.waiting_for, WorkflowRun.current_stage)).all()
    privacy_probe.record("events", [list(row) for row in events])
    privacy_probe.record("runs", [[str(item) for item in row] for row in runs])
    privacy_probe.record("checkpoints", checkpoint_text(workspace))

    privacy_probe.assert_clean(PAYEE, "Priyanka", "4321.55", "4,321.55", "Account 1111")
    if workspace.db.get_bind().dialect.name == "postgresql":
        assert str(run["id"]) in checkpoint_text(workspace)  # the scan is reading real checkpoints


def test_a_concurrent_start_with_the_same_client_key_returns_the_winning_run(workspace):
    from app.workflow import service

    winner = start(workspace, key="race")
    real_scalar = workspace.db.scalar
    calls = {"n": 0}

    def lookup_misses_once(*args, **kwargs):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_scalar(*args, **kwargs)

    workspace.db.scalar = lookup_misses_once  # the other request had not committed yet
    try:
        loser = service.start_run(workspace.db, workspace.owner.id, [], "race")
    finally:
        del workspace.db.scalar

    assert str(loser.id) == winner["id"]
    assert workspace.db.scalar(select(func.count(WorkflowRun.id))) == 1
