from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.finance import create_trip, decide_trip_transaction, trip_detail
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
