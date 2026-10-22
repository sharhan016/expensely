from datetime import date
from decimal import Decimal

from app.models import Transaction


def _add(workspace, account, day, amount, description="SYNTHETIC ROW", *, user=None):
    owner = user or workspace.owner
    workspace.db.add(
        Transaction(
            user_id=owner.id,
            account_id=account.id,
            txn_date=day,
            description=description,
            amount=Decimal(amount),
            dedupe_hash=f"{owner.id}-{account.id}-{day}-{amount}-{description}",
        )
    )
    workspace.db.commit()


def test_running_balance_is_an_exact_decimal_string(workspace):
    account = workspace.account(opening_balance=Decimal("0.10"))
    _add(workspace, account, date(2026, 1, 1), "0.20")
    _add(workspace, account, date(2026, 1, 2), "0.20")

    items = workspace.client.get("/transactions").json()["items"]

    assert [item["running_balance"] for item in items] == ["0.50", "0.30"]


def test_running_balances_are_independent_per_account(workspace):
    first = workspace.account("1111", opening_balance=Decimal("1000"))
    second = workspace.account("2222", opening_balance=Decimal("500"))
    _add(workspace, first, date(2026, 1, 1), "-100.00")
    _add(workspace, second, date(2026, 1, 2), "-50.00")
    _add(workspace, first, date(2026, 1, 3), "250.00")

    by_account = {}
    for item in workspace.client.get("/transactions").json()["items"]:  # newest first
        by_account.setdefault(item["account_id"], item["running_balance"])

    assert by_account == {first.id: "1150.00", second.id: "450.00"}


def test_page_reports_showing_and_total_and_keeps_balances_correct(workspace):
    account = workspace.account(opening_balance=Decimal("0"))
    for day in range(1, 6):
        _add(workspace, account, date(2026, 1, day), "10.00", f"SYNTHETIC ROW {day}")

    first = workspace.client.get("/transactions?limit=2").json()
    second = workspace.client.get("/transactions?limit=2&offset=2").json()

    assert (first["showing"], first["total"], first["limit"], first["offset"]) == (2, 5, 2, 0)
    assert [item["running_balance"] for item in first["items"]] == ["50.00", "40.00"]
    assert [item["running_balance"] for item in second["items"]] == ["30.00", "20.00"]
    assert second["offset"] == 2


def test_more_than_five_hundred_rows_are_reported_not_silently_cut(workspace):
    account = workspace.account(opening_balance=Decimal("0"))
    workspace.db.add_all(
        Transaction(
            user_id=workspace.owner.id,
            account_id=account.id,
            txn_date=date(2026, 1, 1),
            description=f"SYNTHETIC ROW {index}",
            amount=Decimal("1.00"),
            dedupe_hash=f"bulk-{index}",
        )
        for index in range(520)
    )
    workspace.db.commit()

    page = workspace.client.get("/transactions").json()

    assert page["total"] == 520
    assert page["showing"] == len(page["items"]) < page["total"]


def test_date_and_account_filters_apply_to_total(workspace):
    first = workspace.account("1111", opening_balance=Decimal("0"))
    second = workspace.account("2222", opening_balance=Decimal("0"))
    _add(workspace, first, date(2026, 1, 1), "1.00")
    _add(workspace, first, date(2026, 2, 1), "1.00")
    _add(workspace, second, date(2026, 2, 1), "1.00")

    by_date = workspace.client.get("/transactions?date_from=2026-02-01&date_to=2026-02-28").json()
    by_account = workspace.client.get(f"/transactions?account_id={first.id}").json()

    assert by_date["total"] == 2
    assert by_account["total"] == 2
    assert {item["account_id"] for item in by_account["items"]} == {first.id}
    # Filtering must not change the balance a row shows.
    assert by_account["items"][0]["running_balance"] == "2.00"


def test_other_users_rows_are_never_listed_or_counted(workspace):
    foreign = workspace.account(user=workspace.stranger)
    _add(workspace, foreign, date(2026, 1, 1), "5.00", user=workspace.stranger)

    page = workspace.client.get("/transactions").json()

    assert page["total"] == 0
    assert workspace.client.get(f"/transactions?account_id={foreign.id}").json()["total"] == 0


def test_invalid_paging_is_rejected(workspace):
    assert workspace.client.get("/transactions?limit=0").status_code == 422
    assert workspace.client.get("/transactions?limit=501").status_code == 422
    assert workspace.client.get("/transactions?offset=-1").status_code == 422
