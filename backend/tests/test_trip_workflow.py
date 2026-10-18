from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.finance import create_trip, dashboard, decide_trip_transaction, trip_detail
from app.models import Account, Transaction, User
from app.schemas import TripCreate, TripTransactionDecision


def test_trip_finds_date_range_and_persists_review_decisions():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(
            email="traveller@example.test",
            password_hash="not-used",
            display_name="Traveller",
        )
        db.add(user)
        db.flush()
        account = Account(
            user_id=user.id,
            name="Travel card",
            bank_slug="test",
            account_hint="1234",
        )
        db.add(account)
        db.flush()
        before = Transaction(
            user_id=user.id,
            account_id=account.id,
            txn_date=date(2026, 9, 20),
            description="FLIGHT BOOKING",
            amount=Decimal("-12000"),
            dedupe_hash="flight",
        )
        during = Transaction(
            user_id=user.id,
            account_id=account.id,
            txn_date=date(2026, 10, 3),
            description="HOTEL",
            amount=Decimal("-8000"),
            dedupe_hash="hotel",
        )
        outside = Transaction(
            user_id=user.id,
            account_id=account.id,
            txn_date=date(2026, 8, 1),
            description="GROCERIES",
            amount=Decimal("-500"),
            dedupe_hash="groceries",
        )
        db.add_all([before, during, outside])
        db.commit()

        trip = create_trip(
            TripCreate(
                name="Dubai",
                starts_on=date(2026, 10, 1),
                ends_on=date(2026, 10, 7),
                purchase_window_start=date(2026, 9, 1),
            ),
            db,
            user,
        )
        detail = trip_detail(trip.id, db, user)
        assert [item["transaction"].description for item in detail["candidates"]] == [
            "FLIGHT BOOKING",
            "HOTEL",
        ]

        decide_trip_transaction(
            trip.id,
            before.id,
            TripTransactionDecision(status="excluded"),
            db,
            user,
        )
        decide_trip_transaction(
            trip.id,
            during.id,
            TripTransactionDecision(status="included"),
            db,
            user,
        )
        statuses = {
            item["transaction"].description: item["status"]
            for item in trip_detail(trip.id, db, user)["candidates"]
        }
        assert statuses == {"FLIGHT BOOKING": "excluded", "HOTEL": "included"}
        assert db.get(Transaction, during.id).trip_id == trip.id


def test_dashboard_keeps_credit_card_debt_out_of_bank_balance():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(
            email="balances@example.test",
            password_hash="not-used",
            display_name="Balance Test",
        )
        db.add(user)
        db.flush()
        bank = Account(
            user_id=user.id,
            name="Bank account",
            bank_slug="test-bank",
            account_hint="1001",
            opening_balance=Decimal("5000"),
        )
        card = Account(
            user_id=user.id,
            name="Credit card",
            bank_slug="test-card",
            account_hint="2002",
            kind="credit_card",
            opening_balance=Decimal("0"),
        )
        db.add_all([bank, card])
        db.flush()
        db.add_all(
            [
                Transaction(
                    user_id=user.id,
                    account_id=bank.id,
                    txn_date=date(2026, 10, 1),
                    description="BANK CREDIT",
                    amount=Decimal("1000"),
                    dedupe_hash="bank-credit",
                ),
                Transaction(
                    user_id=user.id,
                    account_id=card.id,
                    txn_date=date(2026, 10, 2),
                    description="CARD PURCHASE",
                    amount=Decimal("-750"),
                    dedupe_hash="card-purchase",
                ),
            ]
        )
        db.commit()

        result = dashboard(db, user)
        assert result["bank_balance"] == Decimal("6000")
        assert result["available_balance"] == Decimal("6000")
        assert result["credit_card_balance"] == Decimal("750")
        assert result["account_count"] == 1
        assert result["credit_card_count"] == 1
