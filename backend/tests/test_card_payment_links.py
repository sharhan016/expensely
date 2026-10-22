from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    Account,
    Purpose,
    Transaction,
    TransactionLink,
    TransactionMeaning,
    User,
)
from app.security import create_session
from app.transfer_matching import match_card_payments, match_card_refunds


def _movement(
    *,
    user_id: int,
    account_id: int,
    txn_date: date,
    description: str,
    amount: str,
    fingerprint: str,
) -> Transaction:
    transaction = Transaction(
        user_id=user_id,
        account_id=account_id,
        txn_date=txn_date,
        description=description,
        amount=Decimal(amount),
        dedupe_hash=fingerprint,
    )
    transaction.meaning = TransactionMeaning(
        user_id=user_id,
        purpose=Purpose.OTHER,
        version=1,
        review_status="current",
    )
    return transaction


def test_unique_card_payment_pair_is_neutral_and_reversible():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    owner = User(email="card@example.test", password_hash="unused", display_name="Card Owner")
    stranger = User(email="other@example.test", password_hash="unused", display_name="Other")
    db.add_all([owner, stranger])
    db.flush()
    bank = Account(
        user_id=owner.id,
        name="YES current",
        bank_slug="yes",
        account_hint="XXXX1122",
        kind="current",
    )
    card = Account(
        user_id=owner.id,
        name="YES card",
        bank_slug="yes",
        account_hint="XXXX9405",
        kind="credit_card",
    )
    db.add_all([bank, card])
    db.flush()
    debit = _movement(
        user_id=owner.id,
        account_id=bank.id,
        txn_date=date(2026, 4, 4),
        description="YES BANK CREDIT CARD PAYMENT 9405",
        amount="-500.00",
        fingerprint="bank-card-payment",
    )
    credit = _movement(
        user_id=owner.id,
        account_id=card.id,
        txn_date=date(2026, 4, 5),
        description="PAYMENT RECEIVED - THANK YOU",
        amount="500.00",
        fingerprint="card-payment-credit",
    )
    db.add_all([debit, credit])
    db.commit()

    links = match_card_payments(db, owner.id)
    db.commit()

    assert len(links) == 1
    assert links[0].outflow_transaction_id == debit.id
    assert links[0].inflow_transaction_id == credit.id
    assert debit.meaning.purpose == Purpose.TRANSFER
    assert credit.meaning.purpose == Purpose.TRANSFER
    assert match_card_payments(db, owner.id) == []

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            client.cookies.set("expense_session", create_session(owner.id))
            listed = client.get("/transaction-links")
            assert listed.status_code == 200
            assert listed.json()[0]["amount"] == "500.00"
            assert listed.json()[0]["bank_account_name"] == "YES current"
            assert listed.json()[0]["card_account_name"] == "YES card"
            assert client.get("/transaction-links/unmatched-card-payments").json() == []

            rejected = client.patch(
                f"/transaction-links/{links[0].id}", json={"status": "rejected"}
            )
            assert rejected.status_code == 200
            assert rejected.json()["status"] == "rejected"
            db.refresh(debit.meaning)
            db.refresh(credit.meaning)
            assert debit.meaning.purpose == Purpose.OTHER
            assert credit.meaning.purpose == Purpose.OTHER
            unmatched = client.get("/transaction-links/unmatched-card-payments")
            assert unmatched.status_code == 200
            assert unmatched.json()[0]["transaction_id"] == credit.id
            assert unmatched.json()[0]["amount"] == "500.00"

            client.cookies.set("expense_session", create_session(stranger.id))
            assert client.get("/transaction-links").json() == []
            assert client.get("/transaction-links/unmatched-card-payments").json() == []
            hidden = client.patch(f"/transaction-links/{links[0].id}", json={"status": "rejected"})
            assert hidden.status_code == 404
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_ambiguous_equal_card_payments_are_not_linked():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="ambiguous@example.test", password_hash="unused", display_name="Owner")
        db.add(user)
        db.flush()
        bank = Account(
            user_id=user.id,
            name="Current",
            bank_slug="yes",
            account_hint="1111",
            kind="current",
        )
        card = Account(
            user_id=user.id,
            name="Card",
            bank_slug="yes",
            account_hint="9405",
            kind="credit_card",
        )
        db.add_all([bank, card])
        db.flush()
        db.add_all(
            [
                _movement(
                    user_id=user.id,
                    account_id=bank.id,
                    txn_date=date(2026, 4, day),
                    description=f"CARD PAYMENT candidate {day}",
                    amount="-500.00",
                    fingerprint=f"candidate-{day}",
                )
                for day in (4, 5)
            ]
            + [
                _movement(
                    user_id=user.id,
                    account_id=card.id,
                    txn_date=date(2026, 4, 5),
                    description="PAYMENT RECEIVED",
                    amount="500.00",
                    fingerprint="ambiguous-credit",
                )
            ]
        )
        db.commit()

        assert match_card_payments(db, user.id) == []
        assert db.scalar(select(TransactionLink.id)) is None


def test_unique_card_refund_is_linked_to_purchase_and_reversible():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    owner = User(email="refund@example.test", password_hash="unused", display_name="Owner")
    stranger = User(email="refund-other@example.test", password_hash="unused", display_name="Other")
    db.add_all([owner, stranger])
    db.flush()
    card = Account(
        user_id=owner.id,
        name="YES card",
        bank_slug="yes",
        account_hint="XXXX9405",
        kind="credit_card",
    )
    db.add(card)
    db.flush()
    purchase = _movement(
        user_id=owner.id,
        account_id=card.id,
        txn_date=date(2026, 4, 2),
        description="AMAZON MARKETPLACE",
        amount="-750.00",
        fingerprint="amazon-purchase",
    )
    refund = _movement(
        user_id=owner.id,
        account_id=card.id,
        txn_date=date(2026, 4, 8),
        description="AMAZON REFUND",
        amount="750.00",
        fingerprint="amazon-refund",
    )
    db.add_all([purchase, refund])
    db.commit()

    links = match_card_refunds(db, owner.id)
    db.commit()

    assert len(links) == 1
    assert links[0].link_type == "refund"
    assert links[0].outflow_transaction_id == purchase.id
    assert links[0].inflow_transaction_id == refund.id
    assert refund.meaning.purpose == Purpose.REFUND
    assert purchase.meaning.purpose == Purpose.OTHER

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            client.cookies.set("expense_session", create_session(owner.id))
            listed = client.get("/transaction-links/refunds")
            assert listed.status_code == 200
            assert listed.json()[0]["amount"] == "750.00"
            assert listed.json()[0]["card_account_name"] == "YES card"

            rejected = client.patch(
                f"/transaction-links/refunds/{links[0].id}", json={"status": "rejected"}
            )
            assert rejected.status_code == 200
            db.refresh(refund.meaning)
            assert refund.meaning.purpose == Purpose.OTHER

            client.cookies.set("expense_session", create_session(stranger.id))
            assert client.get("/transaction-links/refunds").json() == []
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_equal_refund_candidates_without_unique_purchase_are_not_linked():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(
            email="refund-ambiguous@example.test", password_hash="unused", display_name="Owner"
        )
        db.add(user)
        db.flush()
        card = Account(
            user_id=user.id,
            name="Card",
            bank_slug="yes",
            account_hint="9405",
            kind="credit_card",
        )
        db.add(card)
        db.flush()
        db.add_all(
            [
                _movement(
                    user_id=user.id,
                    account_id=card.id,
                    txn_date=date(2026, 4, day),
                    description=f"AMAZON ORDER {day}",
                    amount="-500.00",
                    fingerprint=f"amazon-{day}",
                )
                for day in (2, 3)
            ]
            + [
                _movement(
                    user_id=user.id,
                    account_id=card.id,
                    txn_date=date(2026, 4, 8),
                    description="AMAZON REFUND",
                    amount="500.00",
                    fingerprint="ambiguous-refund",
                )
            ]
        )
        db.commit()

        assert match_card_refunds(db, user.id) == []
        assert db.scalar(select(TransactionLink.id)) is None
