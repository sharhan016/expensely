from datetime import date, timedelta
from decimal import Decimal

from app.balances import STALE_AFTER_DAYS, account_balances
from app.models import ImportStatus, Transaction


def _confirmed(workspace, account, end: date, rows=(("-10.00", date(2026, 1, 5)),)):
    statement = workspace.stage(
        [(day, f"SYNTHETIC ROW {index}", amount, "") for index, (amount, day) in enumerate(rows)],
        period=(date(2026, 1, 1), end),
    )
    assert workspace.confirm(statement, account).status_code == 200
    return statement


def test_every_balance_carries_its_statement_date(workspace):
    account = workspace.account(opening_balance=Decimal("100.00"))
    _confirmed(workspace, account, date(2026, 1, 31))

    (balance,) = account_balances(workspace.db, workspace.owner.id, today=date(2026, 2, 10))

    assert balance.balance == Decimal("90.00")
    assert balance.statement_end == date(2026, 1, 31)
    assert balance.as_of == date(2026, 1, 31)
    assert balance.age_days == 10
    assert balance.is_stale is False


def test_an_old_statement_is_reported_stale_not_current(workspace):
    account = workspace.account(opening_balance=Decimal("100.00"))
    _confirmed(workspace, account, date(2026, 1, 31))
    today = date(2026, 1, 31) + timedelta(days=STALE_AFTER_DAYS + 1)

    (balance,) = account_balances(workspace.db, workspace.owner.id, today=today)

    assert balance.is_stale is True


def test_account_with_no_statement_has_no_as_of_date(workspace):
    workspace.account(opening_balance=Decimal("100.00"))

    (balance,) = account_balances(workspace.db, workspace.owner.id, today=date(2026, 2, 10))

    assert balance.as_of is None
    assert balance.is_stale is True


def test_unconfirmed_statements_do_not_set_the_as_of_date(workspace):
    account = workspace.account()
    staged = workspace.stage([(date(2026, 1, 5), "SYNTHETIC ROW", "-1.00", "")])
    assert staged.status == ImportStatus.READY

    (balance,) = account_balances(workspace.db, workspace.owner.id, today=date(2026, 2, 10))

    assert balance.as_of is None
    assert account.id == balance.account_id


def test_dashboard_returns_string_money_with_an_as_of_context(workspace):
    account = workspace.account(opening_balance=Decimal("100.00"))
    _confirmed(workspace, account, date(2026, 1, 31))

    body = workspace.client.get("/dashboard").json()

    assert body["bank_balance"] == "90.00"
    assert body["as_of"] == "2026-01-31"
    assert body["is_stale"] is True  # real "today" is long after January 2026
    assert body["accounts"][0]["statement_end"] == "2026-01-31"
    assert body["accounts"][0]["balance"] == "90.00"


def test_dashboard_as_of_is_the_earliest_account_date(workspace):
    first = workspace.account("1111", opening_balance=Decimal("0"))
    second = workspace.account("2222", opening_balance=Decimal("0"))
    _confirmed(workspace, first, date(2026, 3, 31))
    statement = workspace.stage(
        [(date(2026, 1, 5), "SYNTHETIC ROW", "-1.00", "")],
        hint="2222",
        period=(date(2026, 1, 1), date(2026, 1, 31)),
    )
    workspace.confirm(statement, second)

    assert workspace.client.get("/dashboard").json()["as_of"] == "2026-01-31"


def test_accounts_endpoint_includes_as_of_context(workspace):
    account = workspace.account(opening_balance=Decimal("100.00"))
    _confirmed(workspace, account, date(2026, 1, 31))

    (item,) = workspace.client.get("/accounts").json()

    assert item["current_balance"] == "90.00"
    assert item["as_of"] == "2026-01-31"
    assert item["statement_end"] == "2026-01-31"


def test_assistant_balance_answer_names_the_statement_date(workspace):
    account = workspace.account(opening_balance=Decimal("100.00"))
    _confirmed(workspace, account, date(2026, 1, 31))

    reply = workspace.client.post("/assistant/turn", json={"message": "balance summary"}).json()

    assert "31 Jan 2026" in reply["content"]


def test_balances_ignore_other_users(workspace):
    workspace.account(user=workspace.stranger, opening_balance=Decimal("999.00"))

    assert account_balances(workspace.db, workspace.owner.id, today=date(2026, 2, 1)) == []
    assert isinstance(Transaction.__tablename__, str)
