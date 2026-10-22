from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import Reconciliation, Transaction

JAN = (date(2026, 1, 1), date(2026, 1, 31))


def _extra_movement(workspace, account, amount="-25.00", key="extra"):
    workspace.db.add(
        Transaction(
            user_id=workspace.owner.id,
            account_id=account.id,
            txn_date=date(2026, 1, 15),
            description="SYNTHETIC ROW FROM ANOTHER SOURCE",
            amount=Decimal(amount),
            dedupe_hash=key,
        )
    )
    workspace.db.commit()


def _balanced_january(workspace, account):
    statement = workspace.stage(
        [(date(2026, 1, 5), "SYNTHETIC SHOP", "-100.00", "")],
        opening="1000",
        closing="900",
        period=JAN,
    )
    assert workspace.confirm(statement, account).status_code == 200


def _account_json(workspace, account):
    body = workspace.client.get("/coverage").json()
    return next(item for item in body["accounts"] if item["account_id"] == account.id)


def test_statement_that_adds_up_but_sits_in_a_mismatched_period_is_not_covered(workspace):
    account = workspace.account()
    _extra_movement(workspace, account)  # already in the ledger before the statement is confirmed
    _balanced_january(workspace, account)

    rec = workspace.db.scalar(select(Reconciliation))
    assert rec.status == "balanced"  # the statement itself adds up (1a behavior, unchanged)
    assert rec.period_difference == Decimal("25.00")
    body = _account_json(workspace, account)
    assert body["reconciled_through"] is None
    assert body["first_problem"]["kind"] == "period_mismatch"
    assert "do not add up" in body["summary"]


def test_a_restored_result_never_shows_as_balanced_while_the_period_differs(workspace):
    account = workspace.account()
    _balanced_january(workspace, account)
    assert _account_json(workspace, account)["reconciled_through"] == "2026-01-31"

    _extra_movement(workspace, account)
    assert _account_json(workspace, account)["first_problem"]["kind"] == "invalidated"

    recomputed = workspace.client.post("/coverage/recompute").json()
    body = next(a for a in recomputed["coverage"]["accounts"] if a["account_id"] == account.id)
    # Re-checking restores the statement's own result, but the period still differs.
    assert body["reconciled_through"] is None
    assert body["first_problem"]["kind"] == "period_mismatch"


def test_removing_the_extra_movement_and_rechecking_restores_coverage(workspace):
    account = workspace.account()
    _balanced_january(workspace, account)
    _extra_movement(workspace, account)
    workspace.client.post("/coverage/recompute")
    extra = workspace.db.scalar(select(Transaction).where(Transaction.dedupe_hash == "extra"))

    workspace.db.delete(extra)
    workspace.db.commit()
    result = workspace.client.post("/coverage/recompute").json()

    body = next(a for a in result["coverage"]["accounts"] if a["account_id"] == account.id)
    assert body["reconciled_through"] == "2026-01-31"


def test_row_recorded_without_a_period_difference_reads_as_unverified(workspace):
    account = workspace.account()
    _balanced_january(workspace, account)
    rec = workspace.db.scalar(select(Reconciliation))
    rec.period_difference = None
    workspace.db.commit()

    assert _account_json(workspace, account)["first_problem"]["kind"] == "unverified"
