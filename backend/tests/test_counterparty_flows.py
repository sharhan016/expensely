from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.counterparty_service import extract_counterparty
from app.database import Base, get_db
from app.imports import _stage_parsed
from app.main import app
from app.models import (
    Account,
    Counterparty,
    CounterpartyAlias,
    ImportBatch,
    ImportStatus,
    Purpose,
    StatementImport,
    Transaction,
    TransactionMeaning,
    User,
)
from app.parsers.types import ParsedStatement, ParsedTransaction
from app.security import create_session


def test_upi_handle_is_a_stable_counterparty_identifier():
    first = extract_counterparty("UPI/DR/001/ASHA/YESB/asha.pay@okaxis/REF-1")
    second = extract_counterparty("UPI ASHA.PAY@OKAXIS reference 998877")

    assert first is not None
    assert second is not None
    assert first.alias_type == "upi_handle"
    assert first.alias_value == second.alias_value == "asha.pay@okaxis"
    assert first.display_name == "Asha Pay"
    assert first.confidence == "high"


def test_repeated_upi_flow_groups_without_merging_and_learns_future_meaning():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(email="flow@example.test", password_hash="unused", display_name="Flow Owner")
    db.add(user)
    db.flush()
    account = Account(
        user_id=user.id,
        name="YES current",
        bank_slug="yes",
        account_hint="XXXX9405",
        instrument_type="current",
    )
    batch = ImportBatch(user_id=user.id)
    db.add_all([account, batch])
    db.flush()
    statement = StatementImport(
        batch_id=batch.id,
        user_id=user.id,
        original_filename="yes.csv",
        storage_key="test/yes.csv",
        status=ImportStatus.PROCESSING,
    )
    db.add(statement)
    db.flush()
    start = date(2026, 4, 1)
    parsed = ParsedStatement(
        transactions=[
            ParsedTransaction(
                txn_date=start + timedelta(days=index),
                description=f"UPI/DR/{index:03d}/ASHA/YESB/asha.pay@okaxis/REF-{index}",
                amount=Decimal("-100.00"),
                reference=f"REF-{index}",
            )
            for index in range(10)
        ],
        bank_slug="yes",
        account_name="YES BANK Current Account",
        source_hint="XXXX9405",
        statement_kind="current",
        instrument_confidence="high",
        period_start=start,
        period_end=start + timedelta(days=9),
    )
    _stage_parsed(db, statement, parsed, user.id)
    db.commit()

    assert len(statement.rows) == 10
    assert {row.counterparty_alias_value for row in statement.rows} == {"asha.pay@okaxis"}

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            client.cookies.set("expense_session", create_session(user.id))
            confirmed = client.post(
                f"/imports/{statement.id}/confirm", json={"account_id": account.id}
            )
            assert confirmed.status_code == 200
            assert confirmed.json()["imported"] == 10

            counterparties = client.get("/counterparties")
            assert counterparties.status_code == 200
            assert len(counterparties.json()) == 1
            summary = counterparties.json()[0]
            assert summary["transaction_count"] == 10
            assert summary["unresolved_count"] == 10
            assert Decimal(summary["paid_total"]) == Decimal("1000.00")
            counterparty_id = summary["id"]

            detail = client.get(f"/counterparties/{counterparty_id}")
            assert detail.status_code == 200
            assert len(detail.json()["transactions"]) == 10
            assert len(detail.json()["purpose_totals"]) == 1
            unresolved_total = detail.json()["purpose_totals"][0]
            assert unresolved_total["purpose"] == "other"
            assert Decimal(unresolved_total["paid"]) == Decimal("1000.00")
            assert Decimal(unresolved_total["received"]) == Decimal("0")
            assert unresolved_total["transaction_count"] == 10

            learned = client.patch(
                f"/counterparties/{counterparty_id}",
                json={
                    "display_name": "Asha",
                    "kind": "person",
                    "default_purpose": "personal",
                },
            )
            assert learned.status_code == 200
            assert learned.json()["display_name"] == "Asha"
            assert learned.json()["default_purpose"] == "personal"

            next_batch = ImportBatch(user_id=user.id)
            db.add(next_batch)
            db.flush()
            next_statement = StatementImport(
                batch_id=next_batch.id,
                user_id=user.id,
                original_filename="yes-next.csv",
                storage_key="test/yes-next.csv",
                status=ImportStatus.PROCESSING,
            )
            db.add(next_statement)
            db.flush()
            _stage_parsed(
                db,
                next_statement,
                ParsedStatement(
                    transactions=[
                        ParsedTransaction(
                            txn_date=date(2026, 5, 1),
                            description="UPI/DR/999/ASHA/YESB/asha.pay@okaxis/NEW",
                            amount=Decimal("-250.00"),
                        )
                    ],
                    bank_slug="yes",
                    source_hint="XXXX9405",
                    statement_kind="current",
                    instrument_confidence="high",
                ),
                user.id,
            )
            db.flush()
            next_row = next_statement.rows[0]
            assert next_row.counterparty_id == counterparty_id
            assert next_row.counterparty_name == "Asha"
            assert next_row.purpose == Purpose.PERSONAL
            assert next_row.confidence == "confident"

            assert db.scalar(select(func.count(Transaction.id))) == 10
            assert db.scalar(select(func.count(Counterparty.id))) == 1
            assert db.scalar(select(func.count(CounterpartyAlias.id))) == 1
            assert (
                db.scalar(
                    select(func.count(TransactionMeaning.transaction_id)).where(
                        TransactionMeaning.counterparty_id == counterparty_id
                    )
                )
                == 10
            )
            assert (
                db.scalar(
                    select(func.count(TransactionMeaning.transaction_id)).where(
                        TransactionMeaning.purpose == Purpose.OTHER
                    )
                )
                == 10
            )
    finally:
        app.dependency_overrides.clear()
        db.close()
