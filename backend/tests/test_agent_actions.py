from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.meanings import apply_meaning_change
from app.models import (
    Account,
    Counterparty,
    Purpose,
    Transaction,
    TransactionMeaning,
    User,
)
from app.security import create_session


def test_retrospective_flow_proposal_is_explicit_private_and_reversible():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    owner = User(email="actions@example.test", password_hash="unused", display_name="Owner")
    stranger = User(
        email="actions-other@example.test", password_hash="unused", display_name="Other"
    )
    db.add_all([owner, stranger])
    db.flush()
    account = Account(user_id=owner.id, name="Current", bank_slug="yes", account_hint="7788")
    counterparty = Counterparty(
        user_id=owner.id,
        display_name="Asha",
        kind="person",
        default_purpose=Purpose.PERSONAL,
        is_confirmed=True,
    )
    db.add_all([account, counterparty])
    db.flush()
    transactions = []
    for day, amount in ((1, "-100.00"), (2, "-250.00")):
        transaction = Transaction(
            user_id=owner.id,
            account_id=account.id,
            txn_date=date(2026, 8, day),
            description=f"UPI ASHA RAW {day}",
            amount=Decimal(amount),
            dedupe_hash=f"action-{day}",
            purpose=Purpose.OTHER,
        )
        transaction.meaning = TransactionMeaning(
            user_id=owner.id,
            purpose=Purpose.OTHER,
            counterparty_id=counterparty.id,
            version=1,
            review_status="current",
        )
        transactions.append(transaction)
    db.add_all(transactions)
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            client.cookies.set("expense_session", create_session(owner.id))
            proposed = client.post(
                f"/counterparties/{counterparty.id}/meaning-proposals",
                json={"purpose": "personal"},
            )
            assert proposed.status_code == 201
            action = proposed.json()
            assert action["status"] == "proposed"
            assert action["proposal"]["transaction_count"] == 2
            assert Decimal(action["proposal"]["total"]) == Decimal("350.00")
            assert [transaction.description for transaction in transactions] == [
                "UPI ASHA RAW 1",
                "UPI ASHA RAW 2",
            ]

            repeated = client.post(
                f"/counterparties/{counterparty.id}/meaning-proposals",
                json={"purpose": "personal"},
            )
            assert repeated.json()["id"] == action["id"]

            client.cookies.set("expense_session", create_session(stranger.id))
            assert client.get("/agent-actions").json() == []
            assert (
                client.patch(
                    f"/agent-actions/{action['id']}", json={"status": "accepted"}
                ).status_code
                == 404
            )

            client.cookies.set("expense_session", create_session(owner.id))
            accepted = client.patch(f"/agent-actions/{action['id']}", json={"status": "accepted"})
            assert accepted.status_code == 200
            assert accepted.json()["status"] == "accepted"
            assert all(
                transaction.meaning.purpose == Purpose.PERSONAL for transaction in transactions
            )

            reverted = client.patch(f"/agent-actions/{action['id']}", json={"status": "reverted"})
            assert reverted.status_code == 200
            assert reverted.json()["status"] == "reverted"
            assert all(transaction.meaning.purpose == Purpose.OTHER for transaction in transactions)
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_later_manual_correction_blocks_batch_revert():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(email="stale-action@example.test", password_hash="unused", display_name="Owner")
    db.add(user)
    db.flush()
    account = Account(user_id=user.id, name="Current", bank_slug="yes", account_hint="9911")
    counterparty = Counterparty(user_id=user.id, display_name="Shop", is_confirmed=True)
    db.add_all([account, counterparty])
    db.flush()
    transaction = Transaction(
        user_id=user.id,
        account_id=account.id,
        txn_date=date(2026, 8, 3),
        description="SHOP RAW DESCRIPTION",
        amount=Decimal("-500.00"),
        dedupe_hash="stale-action",
        purpose=Purpose.OTHER,
    )
    transaction.meaning = TransactionMeaning(
        user_id=user.id,
        purpose=Purpose.OTHER,
        counterparty_id=counterparty.id,
        version=1,
        review_status="current",
    )
    db.add(transaction)
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            client.cookies.set("expense_session", create_session(user.id))
            action = client.post(
                f"/counterparties/{counterparty.id}/meaning-proposals",
                json={"purpose": "personal"},
            ).json()
            assert (
                client.patch(
                    f"/agent-actions/{action['id']}", json={"status": "accepted"}
                ).status_code
                == 200
            )

            apply_meaning_change(
                db,
                transaction=transaction,
                actor_user_id=user.id,
                purpose=Purpose.INCOME,
                person_id=None,
                employer_id=None,
                trip_id=None,
                counterparty_id=counterparty.id,
                notes="",
                reason="Manual correction after batch",
            )
            db.commit()

            blocked = client.patch(f"/agent-actions/{action['id']}", json={"status": "reverted"})
            assert blocked.status_code == 409
            assert "corrected later" in blocked.json()["detail"]
            db.refresh(transaction.meaning)
            assert transaction.meaning.purpose == Purpose.INCOME
            assert transaction.description == "SHOP RAW DESCRIPTION"
    finally:
        app.dependency_overrides.clear()
        db.close()
