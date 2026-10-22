import csv
import io
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import (
    Counterparty,
    Purpose,
    Transaction,
    TransactionLink,
    TransactionMeaning,
)
from app.period_report import parse_range

JAN = (date(2026, 1, 1), date(2026, 1, 31))


def add(
    workspace,
    account,
    day,
    amount,
    description,
    purpose=Purpose.OTHER,
    counterparty=None,
    user=None,
    key=None,
):
    owner = user or workspace.owner
    transaction = Transaction(
        user_id=owner.id,
        account_id=account.id,
        txn_date=day,
        description=description,
        amount=Decimal(amount),
        purpose=purpose,
        dedupe_hash=key or f"{description}-{day}-{amount}",
    )
    transaction.meaning = TransactionMeaning(
        user_id=owner.id,
        purpose=purpose,
        counterparty_id=counterparty.id if counterparty else None,
        decision_source="user_confirmed",
    )
    workspace.db.add(transaction)
    workspace.db.commit()
    return transaction


def report(workspace, start="2026-01-01", end="2026-01-31"):
    response = workspace.client.get("/reports/period", params={"from": start, "to": end})
    assert response.status_code == 200, response.text
    return response.json()


def bucket(body, name):
    return body["buckets"][name]


def test_movements_land_in_separate_buckets_and_never_called_spending_when_unexplained(workspace):
    account = workspace.account()
    add(workspace, account, date(2026, 1, 3), "-100.00", "SYNTH SHOP", Purpose.PERSONAL)
    add(workspace, account, date(2026, 1, 4), "5000.00", "SYNTH SALARY", Purpose.INCOME)
    add(workspace, account, date(2026, 1, 5), "-40.00", "SYNTH UNKNOWN")  # purpose unresolved
    add(workspace, account, date(2026, 1, 6), "-70.00", "SYNTH LOAN", Purpose.LOAN_GIVEN)
    add(workspace, account, date(2026, 1, 7), "-30.00", "SYNTH OWN TRANSFER", Purpose.TRANSFER)
    add(workspace, account, date(2026, 1, 8), "20.00", "SYNTH REFUND", Purpose.REFUND)

    body = report(workspace)

    assert bucket(body, "spending") == {"count": 1, "out": "100.00", "in": "0.00"}
    assert bucket(body, "income")["in"] == "5000.00"
    assert bucket(body, "unexplained")["out"] == "40.00"
    assert bucket(body, "obligations")["out"] == "70.00"
    assert bucket(body, "own_places")["out"] == "30.00"
    assert bucket(body, "refunds")["in"] == "20.00"
    assert body["movement_count"] == 6
    assert "spent" not in body["summary"].lower()


def test_linked_card_payment_and_refund_pairs_and_cash_are_neutral(workspace):
    bank = workspace.account("1111")
    card = workspace.account("2222")
    debit = add(workspace, bank, date(2026, 1, 10), "-500.00", "SYNTH CARD PAYMENT OUT")
    credit = add(workspace, card, date(2026, 1, 10), "500.00", "SYNTH PAYMENT RECEIVED")
    purchase = add(workspace, card, date(2026, 1, 11), "-80.00", "SYNTH BUY", Purpose.PERSONAL)
    refund = add(workspace, card, date(2026, 1, 12), "80.00", "SYNTH BUY REVERSAL", Purpose.REFUND)
    atm = add(workspace, bank, date(2026, 1, 13), "-300.00", "ATM WDL SYNTH")
    for link_type, out, inn in (("card_payment", debit, credit), ("reversal", purchase, refund)):
        workspace.db.add(
            TransactionLink(
                user_id=workspace.owner.id,
                link_type=link_type,
                outflow_transaction_id=out.id,
                inflow_transaction_id=inn.id,
                status="confirmed",
            )
        )
    workspace.db.commit()
    workspace.client.get("/cash")  # marks the ATM debit

    body = report(workspace)

    assert bucket(body, "own_places") == {"count": 4, "out": "580.00", "in": "580.00"}
    assert bucket(body, "cash_withdrawal")["out"] == "300.00"
    assert bucket(body, "spending")["count"] == 0
    assert bucket(body, "income")["count"] == 0
    assert atm.id  # marker did not change the movement


def test_every_total_drills_down_to_exactly_its_rows(workspace):
    account = workspace.account()
    add(workspace, account, date(2026, 1, 3), "-100.00", "SYNTH A", Purpose.PERSONAL)
    add(workspace, account, date(2026, 1, 3), "-25.50", "SYNTH B", Purpose.PERSONAL)
    add(workspace, account, date(2026, 1, 9), "-40.00", "SYNTH C")
    body = report(workspace)

    for name, info in body["buckets"].items():
        rows = workspace.client.get(
            "/reports/period/rows",
            params={"from": "2026-01-01", "to": "2026-01-31", "bucket": name, "limit": 500},
        ).json()["items"]
        assert len(rows) == info["count"]
        assert sum(
            (Decimal(r["amount"]) for r in rows if Decimal(r["amount"]) < 0), Decimal(0)
        ) == -Decimal(info["out"])
    day = next(d for d in body["days"] if d["date"] == "2026-01-03")
    day_rows = workspace.client.get(
        "/reports/period/rows",
        params={"from": "2026-01-01", "to": "2026-01-31", "date": "2026-01-03"},
    ).json()["items"]
    assert (day["count"], day["out"]) == (2, "125.50") and len(day_rows) == 2


def test_summary_counts_payments_by_counterparty_and_incoming(workspace):
    account = workspace.account()
    asha = Counterparty(
        user_id=workspace.owner.id, display_name="Asha Synthetic", is_confirmed=True
    )
    shop = Counterparty(user_id=workspace.owner.id, display_name="Corner Shop", is_confirmed=False)
    workspace.db.add_all([asha, shop])
    workspace.db.commit()
    for day in range(1, 4):
        add(
            workspace,
            account,
            date(2026, 1, day),
            "-10.00",
            f"SYNTH ASHA {day}",
            Purpose.PERSONAL,
            asha,
        )
    add(workspace, account, date(2026, 1, 5), "-5.00", "SYNTH SHOP", Purpose.PERSONAL, shop)
    add(workspace, account, date(2026, 1, 6), "99.00", "SYNTH IN", Purpose.INCOME)

    body = report(workspace)

    assert "3 payments to Asha Synthetic" in body["summary"]
    assert "1 incoming" in body["summary"]
    assert "not confirmed" in body["summary"]  # unconfirmed identities are separate
    names = {c["name"]: c["confirmed"] for c in body["counterparties"]}
    assert names == {"Asha Synthetic": True, "Corner Shop": False}


def test_dates_without_checked_statements_are_named_and_the_report_is_partial(workspace):
    account = workspace.account(opening_balance=Decimal("1000"))
    statement = workspace.stage(
        [(date(2026, 1, 5), "SYNTH ROW", "-100.00", "")], opening="1000", closing="900", period=JAN
    )
    workspace.confirm(statement, account)

    inside = report(workspace, "2026-01-05", "2026-01-20")
    partly = report(workspace, "2026-01-20", "2026-02-10")
    outside = report(workspace, "2026-03-01", "2026-03-05")

    assert inside["coverage"]["status"] == "covered"
    assert partly["coverage"]["status"] == "partial"
    assert any("1 Feb 2026 to 10 Feb 2026" in note for note in partly["coverage"]["notes"])
    assert outside["coverage"]["status"] == "partial" and outside["empty"] is True
    assert "no movements" in outside["summary"].lower()


def test_no_accounts_means_no_coverage_claim(workspace):
    body = report(workspace)

    assert body["coverage"]["status"] == "none" and body["empty"] is True


def test_validation_and_authorization(workspace):
    foreign_account = workspace.account(user=workspace.stranger)
    add(
        workspace,
        foreign_account,
        date(2026, 1, 3),
        "-999.00",
        "STRANGER ROW",
        user=workspace.stranger,
    )

    body = report(workspace)

    assert body["movement_count"] == 0
    assert (
        workspace.client.get(
            "/reports/period", params={"from": "2026-02-01", "to": "2026-01-01"}
        ).status_code
        == 422
    )
    assert (
        workspace.client.get(
            "/reports/period", params={"from": "2020-01-01", "to": "2026-01-01"}
        ).status_code
        == 422
    )
    workspace.client.cookies.clear()
    for path in (
        "/reports/period",
        "/reports/period/rows",
        "/reports/period.csv",
        "/reports/period.tsv",
    ):
        assert (
            workspace.client.get(
                path, params={"from": "2026-01-01", "to": "2026-01-31"}
            ).status_code
            == 401
        )


def test_csv_matches_the_rows_and_neutralizes_spreadsheet_formulas(workspace):
    account = workspace.account()
    add(workspace, account, date(2026, 1, 3), "-100.00", '=HYPERLINK("http://x")', Purpose.PERSONAL)
    add(workspace, account, date(2026, 1, 4), "-5.00", "+SYNTH PLUS", Purpose.PERSONAL)
    add(workspace, account, date(2026, 1, 5), "20.00", 'SYNTH, with "quotes"', Purpose.INCOME)
    params = {"from": "2026-01-01", "to": "2026-01-31"}

    response = workspace.client.get("/reports/period.csv", params=params)
    rows = list(csv.reader(io.StringIO(response.text)))

    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert rows[0] == ["Date", "Account", "Description", "Amount", "Bucket", "Counterparty"]
    assert len(rows) == 4
    by_date = {r[0]: r for r in rows[1:]}
    assert by_date["2026-01-03"][2].startswith("'=")  # never executes in Excel
    assert by_date["2026-01-04"][2].startswith("'+")
    assert by_date["2026-01-03"][3] == "-100.00"  # amounts stay plain numbers
    assert by_date["2026-01-05"][2] == 'SYNTH, with "quotes"'
    tsv = workspace.client.get("/reports/period.tsv", params=params).text
    assert [line.split("\t")[0] for line in tsv.strip().split("\n")] == [
        "Date",
        "2026-01-03",
        "2026-01-04",
        "2026-01-05",
    ]


def test_report_endpoints_log_nothing_sensitive(workspace, privacy_probe):
    account = workspace.account()
    add(workspace, account, date(2026, 1, 3), "-4321.55", "ZEBRAQUOKKA PAYEE", Purpose.PERSONAL)
    params = {"from": "2026-01-01", "to": "2026-01-31"}
    for path in (
        "/reports/period",
        "/reports/period/rows",
        "/reports/period.csv",
        "/reports/period.tsv",
    ):
        workspace.client.get(path, params=params)

    privacy_probe.assert_clean("ZEBRAQUOKKA", "4321.55")


@pytest.mark.parametrize(
    ("text", "expected", "assumed"),
    [
        (
            "I want to understand 5 October to 20 October",
            (date(2026, 10, 5), date(2026, 10, 20)),
            True,
        ),
        ("5 Oct 2025 - 20 Oct 2025", (date(2025, 10, 5), date(2025, 10, 20)), False),
        (
            "from 5th October 2026 until 20th October 2026",
            (date(2026, 10, 5), date(2026, 10, 20)),
            False,
        ),
        ("5/10/2026 to 20/10/2026", (date(2026, 10, 5), date(2026, 10, 20)), False),
        ("25 December to 5 January", (date(2025, 12, 25), date(2026, 1, 5)), True),
        ("last month", (date(2026, 9, 1), date(2026, 9, 30)), True),
    ],
)
def test_date_ranges_are_read_deterministically(text, expected, assumed):
    parsed = parse_range(text, today=date(2026, 10, 22))

    assert (parsed.start, parsed.end, parsed.assumed) == (*expected, assumed)


@pytest.mark.parametrize(
    "text", ["hello", "20 October to 5 October", "31 Feb to 2 Mar", "how are you 5 times"]
)
def test_unreadable_or_impossible_ranges_are_not_guessed(text):
    assert parse_range(text, today=date(2026, 10, 22)) is None


def test_assistant_confirms_an_assumed_year_then_reports_and_is_silent_otherwise(workspace):
    account = workspace.account()
    add(workspace, account, date(2026, 1, 3), "-10.00", "SYNTH", Purpose.PERSONAL)

    def send(message):
        return workspace.client.post("/assistant/turn", json={"message": message}).json()

    ask = send("I want to understand 1 January to 31 January")
    assert "I read that as 1 Jan" in ask["content"] and ask["context"]["kind"] == "range_confirm"
    done = send("yes")
    assert done["context"]["kind"] == "period_report"
    assert "1 movement" in done["content"] and "Marked as spending" in done["content"]
    explicit = send("1 January 2026 to 31 January 2026")
    assert explicit["context"]["kind"] == "period_report"  # explicit year: no confirmation
    assert send("yes")["context"]["kind"] != "period_report"  # nothing pending now
    assert workspace.db.scalar(select(func.count(Transaction.id))) == 1
