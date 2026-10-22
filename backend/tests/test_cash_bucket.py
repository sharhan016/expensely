from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.cash import is_cash_withdrawal, neutral_movement_ids
from app.models import CashWithdrawalMarker, Reconciliation, Transaction

JAN = (date(2026, 1, 1), date(2026, 1, 31))


@pytest.mark.parametrize(
    "narration",
    [
        "ATM WDL 400123 SYNTHETIC BRANCH",
        "ATM/CASH WDL/00412",
        "NFS CASH WITHDRAWAL SYNTHETIC",
        "CASH WDL SELF",
        "ATM CASH 5000 SYNTHETIC",
        "atm   wdl  synthetic",
    ],
)
def test_atm_and_cash_withdrawal_narrations_are_marked(narration):
    assert is_cash_withdrawal(narration, Decimal("-500.00")) is True


@pytest.mark.parametrize(
    "narration",
    [
        "UPI/PAYMENT FOR CASH GIFT/SYNTHETIC",
        "UPI/CASHBACK OFFER/SYNTHETIC",
        "CASHFREE PAYMENTS SYNTHETIC",
        "ATM CHARGES SYNTHETIC",
        "ATM WDL FEE SYNTHETIC",
        "ATM WDL REVERSAL SYNTHETIC",
        "SYNTHETIC STORE ATMAKUR",
        "UPI/ATM ROAD STORES/SYNTHETIC",
        "UPI/SODEXO CASH CARD/SYNTHETIC",
        "ANNUAL ATM CARD FEE",
    ],
)
def test_payments_that_merely_mention_cash_or_atm_are_not_marked(narration):
    assert is_cash_withdrawal(narration, Decimal("-500.00")) is False


def test_credits_are_never_withdrawals():
    assert is_cash_withdrawal("ATM WDL REFUND", Decimal("500.00")) is False
    assert is_cash_withdrawal("CASH DEPOSIT ATM", Decimal("500.00")) is False


def _statement_with_atm(workspace, account, withdrawal="-400.00", closing="500.00"):
    statement = workspace.stage(
        [
            (date(2026, 1, 5), "ATM WDL SYNTHETIC BRANCH", withdrawal, ""),
            (date(2026, 1, 9), "UPI SYNTHETIC SHOP", "-100.00", ""),
        ],
        opening="1000",
        closing=closing,
        period=JAN,
    )
    assert workspace.confirm(statement, account).status_code == 200
    return statement


def test_atm_debit_reduces_bank_balance_stays_reconciled_and_is_in_the_bucket(workspace):
    account = workspace.account(opening_balance=Decimal("1000.00"))
    _statement_with_atm(workspace, account)

    cash = workspace.client.get("/cash").json()
    coverage = workspace.client.get("/coverage").json()["accounts"][0]
    dashboard = workspace.client.get("/dashboard").json()

    assert cash["withdrawn_total"] == "400.00"
    assert cash["not_yet_explained"] == "400.00"
    assert cash["since"] == "2026-01-05"
    assert (
        cash["summary"] == "Withdrawn, not yet explained: ₹400.00 since 5 Jan 2026 (1 withdrawal)."
    )
    assert "not a cash balance" in cash["note"]
    assert dashboard["bank_balance"] == "500.00"  # a real debit
    assert coverage["reconciled_through"] == "2026-01-31"  # reconciliation unaffected


def test_withdrawal_is_neutral_and_ordinary_debits_are_not(workspace):
    account = workspace.account(opening_balance=Decimal("1000.00"))
    _statement_with_atm(workspace, account)
    atm = workspace.db.scalar(select(Transaction).where(Transaction.description.like("ATM WDL%")))
    shop = workspace.db.scalar(select(Transaction).where(Transaction.description.like("UPI%")))

    assert neutral_movement_ids(workspace.db, workspace.owner.id) == {atm.id}
    ledger = workspace.client.get("/transactions").json()["items"]
    flags = {item["id"]: item["is_cash_withdrawal"] for item in ledger}
    assert flags == {atm.id: True, shop.id: False}


def test_cash_never_counts_toward_reconciled_through(workspace):
    account = workspace.account(opening_balance=Decimal("1000.00"))
    _statement_with_atm(workspace, account)
    before = workspace.client.get("/coverage").json()

    workspace.client.post(
        "/cash/entries", json={"entry_date": "2026-02-10", "amount": "150.00", "note": "tea"}
    )
    after = workspace.client.get("/coverage").json()

    assert after == before


def test_cash_she_spent_is_her_own_labeled_entry_and_reduces_unexplained(workspace):
    account = workspace.account(opening_balance=Decimal("1000.00"))
    _statement_with_atm(workspace, account)

    response = workspace.client.post(
        "/cash/entries", json={"entry_date": "2026-01-12", "amount": "150.00", "note": "tea"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["not_yet_explained"] == "250.00"
    assert body["explained_by_you"] == "150.00"
    assert body["entries"][0]["label"] == "No statement behind this"
    spent_more = workspace.client.post(
        "/cash/entries", json={"entry_date": "2026-01-13", "amount": "900.00"}
    ).json()
    assert spent_more["not_yet_explained"] == "0.00"  # never negative, never a cash balance
    # Entries are not bank movements.
    assert workspace.db.scalar(select(func.count(Transaction.id))) == 2


def test_unmarking_restores_normal_treatment_and_the_rule_does_not_remark(workspace):
    account = workspace.account(opening_balance=Decimal("1000.00"))
    _statement_with_atm(workspace, account)
    atm = workspace.db.scalar(select(Transaction).where(Transaction.description.like("ATM%")))

    unmarked = workspace.client.put(
        f"/cash/withdrawals/{atm.id}", json={"is_cash_withdrawal": False}
    )

    assert unmarked.json()["withdrawal_count"] == 0
    assert neutral_movement_ids(workspace.db, workspace.owner.id) == set()
    # Opening the summary again must not bring it back.
    assert workspace.client.get("/cash").json()["withdrawal_count"] == 0
    marker = workspace.db.scalar(select(CashWithdrawalMarker))
    assert (marker.is_cash_withdrawal, marker.source) == (False, "user")

    remarked = workspace.client.put(
        f"/cash/withdrawals/{atm.id}", json={"is_cash_withdrawal": True}
    )
    assert remarked.json()["withdrawal_count"] == 1


def test_she_can_mark_a_withdrawal_the_rule_missed(workspace):
    account = workspace.account(opening_balance=Decimal("1000.00"))
    statement = workspace.stage(
        [(date(2026, 1, 5), "SYNTHETIC SELF TRANSFER 77", "-300.00", "")],
        opening="1000",
        closing="700",
        period=JAN,
    )
    workspace.confirm(statement, account)
    movement = workspace.db.scalar(select(Transaction))
    assert workspace.client.get("/cash").json()["withdrawal_count"] == 0

    marked = workspace.client.put(
        f"/cash/withdrawals/{movement.id}", json={"is_cash_withdrawal": True}
    ).json()

    assert marked["withdrawal_count"] == 1
    assert marked["withdrawals"][0]["source"] == "user"


def test_marking_is_idempotent_and_does_not_duplicate_markers(workspace):
    account = workspace.account(opening_balance=Decimal("1000.00"))
    _statement_with_atm(workspace, account)
    atm = workspace.db.scalar(select(Transaction).where(Transaction.description.like("ATM%")))

    for _ in range(3):
        workspace.client.put(f"/cash/withdrawals/{atm.id}", json={"is_cash_withdrawal": True})
        workspace.client.get("/cash")

    assert workspace.db.scalar(select(func.count(CashWithdrawalMarker.id))) == 1
    assert workspace.db.scalar(select(func.count(Reconciliation.id))) == 1


def test_only_bank_debits_can_be_marked(workspace):
    account = workspace.account(opening_balance=Decimal("0"))
    statement = workspace.stage([(date(2026, 1, 5), "SYNTHETIC SALARY", "100.00", "")], period=JAN)
    workspace.confirm(statement, account)
    credit = workspace.db.scalar(select(Transaction))

    response = workspace.client.put(
        f"/cash/withdrawals/{credit.id}", json={"is_cash_withdrawal": True}
    )

    assert response.status_code == 422


def test_cash_endpoints_hide_foreign_records(workspace):
    foreign_account = workspace.account(user=workspace.stranger)
    foreign = Transaction(
        user_id=workspace.stranger.id,
        account_id=foreign_account.id,
        txn_date=date(2026, 1, 5),
        description="ATM WDL SYNTHETIC",
        amount=Decimal("-50.00"),
        dedupe_hash="foreign-atm",
    )
    workspace.db.add(foreign)
    workspace.db.commit()

    assert workspace.client.get("/cash").json()["withdrawal_count"] == 0
    assert (
        workspace.client.put(
            f"/cash/withdrawals/{foreign.id}", json={"is_cash_withdrawal": True}
        ).status_code
        == 404
    )
    assert workspace.client.delete("/cash/entries/999").status_code == 404
    assert workspace.db.scalar(select(func.count(CashWithdrawalMarker.id))) == 0


def test_entry_validation(workspace):
    assert (
        workspace.client.post(
            "/cash/entries", json={"entry_date": "2026-01-12", "amount": "0"}
        ).status_code
        == 422
    )


def test_cash_endpoints_log_nothing_sensitive(workspace, privacy_probe):
    account = workspace.account(opening_balance=Decimal("1000.00"))
    _statement_with_atm(workspace, account)
    workspace.client.post(
        "/cash/entries", json={"entry_date": "2026-01-12", "amount": "150.00", "note": "ZEBRATEA"}
    )
    workspace.client.get("/cash")

    privacy_probe.assert_clean("SYNTHETIC BRANCH", "ZEBRATEA", "400.00", "150.00")


def test_credit_card_debits_are_never_cash_withdrawals(workspace):
    from app.models import Account

    card = Account(
        user_id=workspace.owner.id,
        name="Synthetic card",
        bank_slug="test",
        account_hint="9999",
        instrument_type="credit_card",
    )
    workspace.db.add(card)
    workspace.db.flush()
    workspace.db.add(
        Transaction(
            user_id=workspace.owner.id,
            account_id=card.id,
            txn_date=date(2026, 1, 5),
            description="ATM WDL CASH ADVANCE SYNTHETIC",
            amount=Decimal("-500.00"),
            dedupe_hash="card-cash-advance",
        )
    )
    workspace.db.commit()

    assert workspace.client.get("/cash").json()["withdrawal_count"] == 0


def test_assistant_answers_cash_and_coverage_questions_plainly(workspace):
    account = workspace.account(opening_balance=Decimal("1000.00"))
    _statement_with_atm(workspace, account)

    cash = workspace.client.post("/assistant/turn", json={"message": "how much cash?"}).json()
    covered = workspace.client.post(
        "/assistant/turn", json={"message": "what is reconciled?"}
    ).json()
    summary = workspace.client.post("/assistant/turn", json={"message": "balance summary"}).json()

    assert "Withdrawn, not yet explained: ₹400.00 since 5 Jan 2026" in cash["content"]
    assert "not a cash balance" in cash["content"]
    assert "reconciled through 31 Jan 2026" in covered["content"]
    assert "reconciled through 31 Jan 2026" in summary["content"]
    assert "Withdrawn, not yet explained" in summary["content"]
