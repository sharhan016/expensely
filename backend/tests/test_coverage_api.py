from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.models import (
    Account,
    ImportStatus,
    Reconciliation,
    StatementImport,
    Transaction,
)


def month(workspace, account, start, end, opening, closing, *, hint="1111", **extra):
    """One confirmed statement with a single movement that makes opening reach closing."""
    amount = Decimal(closing) - Decimal(opening)
    statement = workspace.stage(
        [(start, f"SYNTHETIC ROW {start.isoformat()}", f"{amount:.2f}", "")],
        hint=hint,
        opening=opening,
        closing=closing,
        period=(start, end),
    )
    response = workspace.confirm(statement, account, **extra)
    assert response.status_code == 200, response.text
    return statement


def report(workspace):
    return workspace.client.get("/coverage").json()


def acct(body, account):
    return next(item for item in body["accounts"] if item["account_id"] == account.id)


JAN = (date(2026, 1, 1), date(2026, 1, 31))
FEB = (date(2026, 2, 1), date(2026, 2, 28))
MAR = (date(2026, 3, 1), date(2026, 3, 31))
APR_LATE = (date(2026, 4, 5), date(2026, 4, 30))


def test_connected_statements_reconcile_through_the_later_end(workspace):
    account = workspace.account()
    month(workspace, account, *JAN, "1000", "900")
    month(workspace, account, *FEB, "900", "800")

    body = report(workspace)

    assert acct(body, account)["reconciled_through"] == "2026-02-28"
    assert body["overall"]["reconciled_through"] == "2026-02-28"
    assert [s["kind"] for s in acct(body, account)["segments"]] == ["covered", "covered"]


def test_gap_with_matching_balances_is_bridged_and_can_be_unbridged(workspace):
    account = workspace.account()
    month(workspace, account, *JAN, "1000", "900")
    month(workspace, account, *APR_LATE, "900", "800")

    body = acct(report(workspace), account)
    assert body["reconciled_through"] == "2026-04-30"
    bridged = [s for s in body["segments"] if s["kind"] == "bridged"]
    assert [(s["start"], s["end"]) for s in bridged] == [("2026-02-01", "2026-04-04")]

    gap = {"gap_start": "2026-02-01", "gap_end": "2026-04-04"}
    unbridged = workspace.client.post(f"/coverage/accounts/{account.id}/unbridged-gaps", json=gap)
    assert unbridged.status_code == 200
    assert acct(unbridged.json(), account)["reconciled_through"] == "2026-01-31"
    flagged = [s for s in acct(unbridged.json(), account)["segments"] if s["unbridged"]]
    assert [(s["kind"], s["start"]) for s in flagged] == [("gap", "2026-02-01")]
    # Doing it twice changes nothing.
    again = workspace.client.post(f"/coverage/accounts/{account.id}/unbridged-gaps", json=gap)
    assert acct(again.json(), account)["reconciled_through"] == "2026-01-31"

    rebridged = workspace.client.delete(
        f"/coverage/accounts/{account.id}/unbridged-gaps", params=gap
    )
    assert rebridged.status_code == 204
    assert acct(report(workspace), account)["reconciled_through"] == "2026-04-30"


def test_unbridging_something_that_is_not_a_bridged_gap_is_rejected(workspace):
    account = workspace.account()
    month(workspace, account, *JAN, "1000", "900")

    response = workspace.client.post(
        f"/coverage/accounts/{account.id}/unbridged-gaps",
        json={"gap_start": "2026-05-01", "gap_end": "2026-05-04"},
    )

    assert response.status_code == 422


def test_gap_with_different_balances_is_a_discontinuity(workspace):
    account = workspace.account()
    month(workspace, account, *JAN, "1000", "900")
    month(workspace, account, *APR_LATE, "850", "800")

    body = acct(report(workspace), account)

    assert body["reconciled_through"] == "2026-01-31"
    assert body["first_problem"]["kind"] == "discontinuity"
    assert "do not connect" in body["summary"]


def test_touching_statements_with_different_balances_stop_the_chain(workspace):
    account = workspace.account()
    month(workspace, account, *JAN, "1000", "900")
    month(workspace, account, *FEB, "890", "800")

    body = acct(report(workspace), account)

    assert body["reconciled_through"] == "2026-01-31"
    assert body["first_problem"]["kind"] == "discontinuity"


def test_layout_without_balance_endpoints_is_covered_but_not_verifiable(workspace):
    account = workspace.account()
    month(workspace, account, *JAN, "1000", "900")
    unverifiable = workspace.stage([(date(2026, 2, 3), "SYNTHETIC ROW", "-5.00", "")], period=FEB)
    assert workspace.confirm(unverifiable, account).status_code == 200

    body = acct(report(workspace), account)

    assert body["reconciled_through"] == "2026-01-31"
    assert body["first_problem"]["kind"] == "unverifiable"


def test_statement_confirmed_with_a_difference_never_counts(workspace):
    account = workspace.account()
    month(workspace, account, *JAN, "1000", "900")
    statement = workspace.stage(
        [(date(2026, 2, 3), "SYNTHETIC ROW", "-50.00", "")],
        opening="900",
        closing="800",
        period=FEB,
    )
    assert (
        workspace.confirm(statement, account, allow_reconciliation_difference=True).status_code
        == 200
    )

    body = acct(report(workspace), account)

    assert body["reconciled_through"] == "2026-01-31"
    assert body["first_problem"]["kind"] == "difference"


def test_movement_added_inside_a_reconciled_period_invalidates_it(workspace):
    account = workspace.account()
    month(workspace, account, *JAN, "1000", "900")
    month(workspace, account, *FEB, "900", "800")
    before = workspace.db.scalar(
        select(Reconciliation).where(Reconciliation.period_start == FEB[0])
    )
    assert before.invalidated_at is None

    workspace.db.add(
        Transaction(
            user_id=workspace.owner.id,
            account_id=account.id,
            txn_date=date(2026, 2, 10),
            description="SYNTHETIC LATE ROW",
            amount=Decimal("-1.00"),
            dedupe_hash="late-row",
        )
    )
    workspace.db.commit()

    body = acct(report(workspace), account)
    assert body["reconciled_through"] == "2026-01-31"
    assert body["first_problem"]["kind"] == "invalidated"
    workspace.db.refresh(before)
    assert before.invalidated_at is not None
    assert "added, removed or changed" in before.invalidation_reason
    # January was untouched and stays reconciled.
    jan = workspace.db.scalar(select(Reconciliation).where(Reconciliation.period_start == JAN[0]))
    assert jan.invalidated_at is None


def test_movement_removed_or_changed_inside_a_reconciled_period_invalidates_it(workspace):
    account = workspace.account()
    month(workspace, account, *JAN, "1000", "900")
    movement = workspace.db.scalar(select(Transaction))

    movement.amount = Decimal("-99.00")
    workspace.db.commit()
    assert acct(report(workspace), account)["first_problem"]["kind"] == "invalidated"

    recomputed = workspace.client.post("/coverage/recompute").json()
    assert recomputed["rechecked"] == 1
    # The changed amount no longer adds up, so re-checking reports a difference, not a repair.
    assert acct(recomputed["coverage"], account)["first_problem"]["kind"] == "difference"

    workspace.db.delete(movement)
    workspace.db.commit()
    assert acct(report(workspace), account)["reconciled_through"] is None


def test_meaning_only_edits_do_not_invalidate(workspace):
    account = workspace.account()
    month(workspace, account, *JAN, "1000", "900")
    movement = workspace.db.scalar(select(Transaction))

    response = workspace.client.patch(
        f"/transactions/{movement.id}",
        json={"purpose": "personal", "notes": "ok", "expected_version": 1},
    )

    assert response.status_code == 200
    assert acct(report(workspace), account)["reconciled_through"] == "2026-01-31"


def test_overall_names_the_lagging_account(workspace):
    first = workspace.account("1111")
    second = workspace.account("2222")
    month(workspace, first, *JAN, "1000", "900", hint="1111")
    month(workspace, first, *FEB, "900", "800", hint="1111")
    month(workspace, first, *MAR, "800", "700", hint="1111")
    month(workspace, second, *JAN, "500", "400", hint="2222")
    month(workspace, second, *FEB, "400", "300", hint="2222")

    body = report(workspace)

    assert acct(body, first)["reconciled_through"] == "2026-03-31"
    assert body["overall"]["reconciled_through"] == "2026-02-28"
    assert body["overall"]["lagging_name"] == second.name
    assert second.name in body["overall"]["summary"]


def test_out_of_scope_account_is_left_out_of_the_overall_line(workspace):
    first = workspace.account("1111")
    second = workspace.account("2222")
    month(workspace, first, *JAN, "1000", "900", hint="1111")
    month(workspace, second, *JAN, "500", "400", hint="2222")
    month(workspace, second, *FEB, "400", "300", hint="2222")
    month(workspace, first, *FEB, "900", "800", hint="1111")
    month(workspace, first, *MAR, "800", "700", hint="1111")

    patched = workspace.client.patch(f"/coverage/accounts/{second.id}", json={"in_scope": False})

    assert patched.json()["overall"]["reconciled_through"] == "2026-03-31"
    assert patched.json()["overall"]["in_scope_count"] == 1
    workspace.db.refresh(second)
    assert second.in_scope is False


def test_changing_records_start_recomputes_without_touching_movements(workspace):
    account = workspace.account()
    month(workspace, account, date(2025, 6, 1), date(2025, 6, 30), "500", "480")
    month(workspace, account, *JAN, "1000", "900")
    month(workspace, account, *FEB, "900", "800")
    snapshot = lambda: workspace.db.execute(  # noqa: E731
        select(func.count(Transaction.id), func.sum(Transaction.amount))
    ).one()
    movements_before = snapshot()
    unanchored = acct(report(workspace), account)
    patched = workspace.client.patch(
        f"/coverage/accounts/{account.id}",
        json={"records_start": "2026-01-01", "records_opening_balance": "1000.00"},
    )

    anchored = acct(patched.json(), account)
    assert anchored["records_start"] == "2026-01-01"
    assert anchored["records_start_is_default"] is False
    assert anchored["records_opening_balance"] == "1000.00"
    assert anchored["reconciled_through"] == "2026-02-28"
    assert snapshot() == movements_before
    assert unanchored["records_start_is_default"] is True

    reset = workspace.client.patch(f"/coverage/accounts/{account.id}", json={"records_start": None})
    assert acct(reset.json(), account)["records_start_is_default"] is True


def test_legacy_reconciliation_is_unverified_until_recomputed(workspace):
    account = workspace.account()
    statement = month(workspace, account, *JAN, "1000", "900")
    rec = workspace.db.scalar(select(Reconciliation))
    rec.movement_fingerprint = None  # as migrated rows are
    rec.source_statement_id = None
    workspace.db.commit()

    assert acct(report(workspace), account)["first_problem"]["kind"] == "unverified"

    result = workspace.client.post("/coverage/recompute").json()
    assert (result["rechecked"], result["balanced"]) == (1, 1)
    assert acct(result["coverage"], account)["reconciled_through"] == "2026-01-31"
    workspace.db.refresh(rec)
    assert rec.source_statement_id == statement.id and rec.movement_fingerprint


def test_recompute_is_idempotent_and_agrees_with_confirm(workspace):
    account = workspace.account()
    month(workspace, account, *JAN, "1000", "900")
    month(workspace, account, *FEB, "900", "800")
    first = workspace.client.post("/coverage/recompute").json()
    rows_after_first = workspace.db.scalar(select(func.count(Reconciliation.id)))
    second = workspace.client.post("/coverage/recompute").json()

    assert first == second
    assert workspace.db.scalar(select(func.count(Reconciliation.id))) == rows_after_first == 2
    assert acct(second["coverage"], account)["reconciled_through"] == "2026-02-28"


def test_reuploading_a_statement_keeps_coverage_stable(workspace):
    account = workspace.account()
    month(workspace, account, *JAN, "1000", "900")
    before = report(workspace)

    again = workspace.stage(
        [(JAN[0], f"SYNTHETIC ROW {JAN[0].isoformat()}", "-100.00", "")],
        opening="1000",
        closing="900",
        period=JAN,
    )
    assert workspace.confirm(again, account).json()["imported"] == 0

    assert (
        acct(report(workspace), account)["reconciled_through"]
        == acct(before, account)["reconciled_through"]
    )


def test_cards_are_created_out_of_scope(workspace):
    statement = workspace.stage([(JAN[0], "SYNTHETIC CARD ROW", "-5.00", "")], hint="7777")
    statement.statement_kind = "credit_card"
    workspace.db.commit()

    created = workspace.confirm(statement, None, account_name="Synthetic card", bank_slug="test")

    assert created.status_code == 200
    card = workspace.db.get(Account, created.json()["account_id"])
    assert card.in_scope is False
    assert workspace.db.get(StatementImport, statement.id).status == ImportStatus.CONFIRMED


def test_every_coverage_endpoint_hides_foreign_accounts(workspace):
    foreign = workspace.account(user=workspace.stranger)
    body = {"gap_start": "2026-02-01", "gap_end": "2026-02-04"}

    assert report(workspace)["accounts"] == []
    assert (
        workspace.client.patch(
            f"/coverage/accounts/{foreign.id}", json={"in_scope": False}
        ).status_code
        == 404
    )
    assert (
        workspace.client.post(
            f"/coverage/accounts/{foreign.id}/unbridged-gaps", json=body
        ).status_code
        == 404
    )
    assert (
        workspace.client.delete(
            f"/coverage/accounts/{foreign.id}/unbridged-gaps", params=body
        ).status_code
        == 404
    )
    workspace.db.refresh(foreign)
    assert foreign.in_scope is True


def test_coverage_endpoints_log_nothing_sensitive(workspace, privacy_probe):
    account = workspace.account()
    month(workspace, account, *JAN, "1000", "900")
    month(workspace, account, *APR_LATE, "900", "800")

    for _ in range(2):
        workspace.client.get("/coverage")
        workspace.client.post("/coverage/recompute")
    workspace.client.patch(f"/coverage/accounts/{account.id}", json={"records_start": "2026-01-01"})

    privacy_probe.assert_clean("SYNTHETIC ROW", "900.00", "1000.00")
