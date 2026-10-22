import random
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import Purpose, Transaction, TransactionLink, TransactionMeaning

START, END = "2026-01-01", "2026-01-31"


def add(workspace, account, day, amount, purpose, key, user=None):
    owner = user or workspace.owner
    transaction = Transaction(
        user_id=owner.id,
        account_id=account.id,
        txn_date=day,
        description=f"SYNTH {key}",
        amount=Decimal(amount),
        purpose=purpose,
        dedupe_hash=key,
    )
    transaction.meaning = TransactionMeaning(
        user_id=owner.id, purpose=purpose, decision_source="user_confirmed"
    )
    workspace.db.add(transaction)
    workspace.db.flush()
    return transaction


def all_rows(workspace, **params):
    items, offset = [], 0
    while True:
        page = workspace.client.get(
            "/reports/period/rows",
            params={"from": START, "to": END, "limit": 500, "offset": offset, **params},
        ).json()
        items += page["items"]
        offset += page["showing"]
        if offset >= page["total"]:
            return items


# ---- (a) dashboard and report share one service -------------------------------------------------


def _mixed_dataset(workspace):
    bank, card = workspace.account("1111"), workspace.account("2222")
    add(workspace, bank, date(2026, 1, 3), "-100.00", Purpose.PERSONAL, "shop")
    add(workspace, bank, date(2026, 1, 4), "5000.00", Purpose.INCOME, "pay")
    add(workspace, bank, date(2026, 1, 5), "-40.00", Purpose.OTHER, "unknown")
    add(workspace, bank, date(2026, 1, 6), "-70.00", Purpose.LOAN_GIVEN, "loan")
    add(workspace, bank, date(2026, 1, 7), "-30.00", Purpose.TRANSFER, "own")
    debit = add(workspace, bank, date(2026, 1, 10), "-500.00", Purpose.OTHER, "cardpay")
    credit = add(workspace, card, date(2026, 1, 10), "500.00", Purpose.OTHER, "cardcredit")
    add(workspace, bank, date(2026, 1, 13), "-300.00", Purpose.OTHER, "ATM WDL SYNTH")
    workspace.db.add(
        TransactionLink(
            user_id=workspace.owner.id,
            link_type="card_payment",
            outflow_transaction_id=debit.id,
            inflow_transaction_id=credit.id,
            status="confirmed",
        )
    )
    workspace.db.commit()
    workspace.client.get("/cash")  # marks the ATM debit


def test_dashboard_and_report_give_identical_numbers_for_one_dataset(workspace):
    _mixed_dataset(workspace)
    params = {"from": START, "to": END}

    report = workspace.client.get("/reports/period", params=params).json()
    dashboard = workspace.client.get("/dashboard", params=params).json()

    assert dashboard["period"]["start"] == START and dashboard["period"]["end"] == END
    assert dashboard["period"]["buckets"] == report["buckets"]
    assert dashboard["period"]["labels"] == report["labels"]
    assert dashboard["period"]["movement_count"] == report["movement_count"]
    spending = dashboard["period"]["buckets"]["spending"]
    assert (spending["count"], spending["out"]) == (1, "100.00")


def test_dashboard_defaults_to_the_saved_review_period(workspace):
    _mixed_dataset(workspace)
    workspace.client.put("/review-period", json={"period_start": START, "period_end": END})

    dashboard = workspace.client.get("/dashboard").json()

    assert (dashboard["period"]["start"], dashboard["period"]["end"]) == (START, END)
    assert dashboard["period"]["buckets"]["spending"]["count"] == 1


# ---- (b) increment 3 hardening ------------------------------------------------------------------


def test_every_total_equals_the_sum_of_its_drill_down_rows_on_random_data(workspace):
    rng = random.Random(7)
    bank, card = workspace.account("1111"), workspace.account("2222")
    purposes = list(Purpose)
    made = []
    for index in range(150):
        amount = Decimal(rng.randint(1, 500000)) / 100 * rng.choice([-1, 1])
        account = rng.choice([bank, card])
        made.append(
            add(
                workspace,
                account,
                date(2026, 1, rng.randint(1, 31)),
                f"{amount:.2f}",
                rng.choice(purposes),
                f"r{index}",
            )
        )
    for pair in range(0, 20, 2):
        workspace.db.add(
            TransactionLink(
                user_id=workspace.owner.id,
                link_type=rng.choice(["card_payment", "refund"]),
                outflow_transaction_id=made[pair].id,
                inflow_transaction_id=made[pair + 1].id,
                status="confirmed",
            )
        )
    workspace.db.commit()

    body = workspace.client.get("/reports/period", params={"from": START, "to": END}).json()

    total_rows = 0
    for name, info in body["buckets"].items():
        rows = all_rows(workspace, bucket=name)
        amounts = [Decimal(row["amount"]) for row in rows]
        assert len(rows) == info["count"]
        assert -sum((a for a in amounts if a < 0), Decimal(0)) == Decimal(info["out"])
        assert sum((a for a in amounts if a > 0), Decimal(0)) == Decimal(info["in"])
        total_rows += len(rows)
    assert total_rows == body["movement_count"] == 150
    for day in body["days"]:
        day_rows = all_rows(workspace, date=day["date"])
        assert len(day_rows) == day["count"]
        assert -sum(
            (Decimal(r["amount"]) for r in day_rows if Decimal(r["amount"]) < 0), Decimal(0)
        ) == Decimal(day["out"])


def test_a_range_with_no_data_says_so_and_asks_for_statements(workspace):
    workspace.account()

    body = workspace.client.get("/reports/period", params={"from": START, "to": END}).json()

    assert body["empty"] is True
    assert "no movements recorded" in body["summary"]
    assert "Send me the statements that cover these dates" in body["summary"]


def test_large_result_sets_page_and_say_how_much_is_shown(workspace):
    account = workspace.account()
    workspace.db.add_all(
        Transaction(
            user_id=workspace.owner.id,
            account_id=account.id,
            txn_date=date(2026, 1, 1 + i % 28),
            description=f"SYNTH {i}",
            amount=Decimal("-1.00"),
            purpose=Purpose.PERSONAL,
            dedupe_hash=f"bulk{i}",
        )
        for i in range(520)
    )
    workspace.db.commit()
    params = {"from": START, "to": END}

    first = workspace.client.get("/reports/period/rows", params=params).json()
    second = workspace.client.get("/reports/period/rows", params={**params, "offset": 200}).json()
    export = workspace.client.get("/reports/period.csv", params=params).text

    assert (first["showing"], first["total"], first["limit"], first["offset"]) == (200, 520, 200, 0)
    assert (second["showing"], second["offset"]) == (200, 200)
    assert len({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}) == 0
    assert len(export.strip().split("\n")) == 521  # exports are complete, never truncated
    assert (
        workspace.client.get("/reports/period/rows", params={**params, "limit": 501}).status_code
        == 422
    )
    assert workspace.db.scalar(select(Transaction.id).limit(1)) is not None
