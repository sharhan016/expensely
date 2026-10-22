from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import Reconciliation, Transaction

START, END = date(2026, 3, 1), date(2026, 3, 31)


def _manual_movement(workspace, account, amount: str):
    workspace.db.add(
        Transaction(
            user_id=workspace.owner.id,
            account_id=account.id,
            txn_date=date(2026, 3, 15),
            description="SYNTHETIC OTHER STATEMENT ROW",
            amount=Decimal(amount),
            dedupe_hash="synthetic-other-statement-row",
        )
    )
    workspace.db.commit()


def _stage(workspace):
    return workspace.stage(
        [
            (date(2026, 3, 2), "SYNTHETIC SHOP", "-100.00", "R1"),
            (date(2026, 3, 3), "SYNTHETIC CAFE", "-50.00", "R2"),
        ],
        opening="1000.00",
        closing="850.00",
        period=(START, END),
    )


def test_statement_reconciles_on_its_own_movements_not_the_whole_period(workspace):
    account = workspace.account()
    _manual_movement(workspace, account, "-25.00")

    response = workspace.confirm(_stage(workspace), account)

    assert response.status_code == 200
    reconciliation = workspace.db.scalar(select(Reconciliation))
    assert reconciliation.status == "balanced"
    body = response.json()
    assert Decimal(body["statement_difference"]) == 0
    # The whole-period view is reported separately and shows the extra movement.
    assert Decimal(body["period_difference"]) == Decimal("25.00")


def test_reimported_statement_still_reconciles_through_matched_ledger_rows(workspace):
    account = workspace.account()
    workspace.confirm(_stage(workspace), account)

    again = workspace.confirm(_stage(workspace), account)

    assert again.json()["imported"] == 0
    assert Decimal(again.json()["statement_difference"]) == 0
    assert Decimal(again.json()["period_difference"]) == 0
    assert workspace.db.scalar(select(Reconciliation)).status == "balanced"


def test_statement_with_a_real_difference_stays_unreconciled(workspace):
    account = workspace.account()
    statement = workspace.stage(
        [(date(2026, 3, 2), "SYNTHETIC SHOP", "-100.00", "R1")],
        opening="1000.00",
        closing="850.00",
        period=(START, END),
    )

    response = workspace.confirm(statement, account, allow_reconciliation_difference=True)

    assert Decimal(response.json()["statement_difference"]) == Decimal("-50.00")
    assert workspace.db.scalar(select(Reconciliation)).status == "difference"
