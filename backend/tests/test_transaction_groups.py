from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.grouping import sync_transaction_group
from app.main import app
from app.meanings import apply_meaning_change
from app.models import (
    Account,
    Counterparty,
    Person,
    Purpose,
    Transaction,
    TransactionGroup,
    TransactionGroupMember,
    TransactionMeaning,
    User,
)
from app.security import create_session


def _transaction(user_id, account_id, counterparty_id, purpose, amount, suffix, person_id=None):
    transaction = Transaction(
        user_id=user_id,
        account_id=account_id,
        txn_date=date(2026, 5, int(suffix)),
        description=f"ASHA PAYMENT {suffix}",
        amount=Decimal(amount),
        dedupe_hash=f"group-{suffix}",
        purpose=purpose,
        person_id=person_id,
    )
    transaction.meaning = TransactionMeaning(
        user_id=user_id,
        purpose=purpose,
        person_id=person_id,
        counterparty_id=counterparty_id,
        version=1,
        review_status="current",
    )
    return transaction


def test_groups_reference_each_movement_and_split_financial_meaning():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    owner = User(email="groups@example.test", password_hash="unused", display_name="Owner")
    stranger = User(email="groups-other@example.test", password_hash="unused", display_name="Other")
    db.add_all([owner, stranger])
    db.flush()
    account = Account(
        user_id=owner.id,
        name="Current",
        bank_slug="yes",
        account_hint="7788",
        kind="current",
    )
    counterparty = Counterparty(user_id=owner.id, display_name="Asha", kind="person")
    person = Person(user_id=owner.id, name="Asha")
    db.add_all([account, counterparty, person])
    db.flush()
    transactions = [
        _transaction(
            owner.id,
            account.id,
            counterparty.id,
            Purpose.PERSONAL,
            "-100.00",
            "1",
        ),
        _transaction(
            owner.id,
            account.id,
            counterparty.id,
            Purpose.PERSONAL,
            "-200.00",
            "2",
        ),
        _transaction(
            owner.id,
            account.id,
            counterparty.id,
            Purpose.LOAN_GIVEN,
            "-300.00",
            "3",
            person.id,
        ),
    ]
    db.add_all(transactions)
    db.flush()
    for transaction in transactions:
        sync_transaction_group(db, transaction)
    db.commit()

    groups = db.scalars(select(TransactionGroup).order_by(TransactionGroup.purpose)).all()
    assert len(groups) == 2
    assert db.query(TransactionGroupMember).count() == 3
    assert [transaction.description for transaction in transactions] == [
        "ASHA PAYMENT 1",
        "ASHA PAYMENT 2",
        "ASHA PAYMENT 3",
    ]

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            client.cookies.set("expense_session", create_session(owner.id))
            listed = client.get("/transaction-groups")
            assert listed.status_code == 200
            totals = {
                item["purpose"]: (item["transaction_count"], Decimal(item["total"]))
                for item in listed.json()
            }
            assert totals == {
                "personal": (2, Decimal("300.00")),
                "loan_given": (1, Decimal("300.00")),
            }

            loan_group = next(item for item in listed.json() if item["purpose"] == "loan_given")
            detail = client.get(f"/transaction-groups/{loan_group['id']}")
            assert detail.status_code == 200
            assert [row["description"] for row in detail.json()["transactions"]] == [
                "ASHA PAYMENT 3"
            ]

            client.cookies.set("expense_session", create_session(stranger.id))
            assert client.get("/transaction-groups").json() == []
            assert client.get(f"/transaction-groups/{loan_group['id']}").status_code == 404
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_meaning_correction_moves_only_group_membership():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="move-group@example.test", password_hash="unused", display_name="Owner")
        db.add(user)
        db.flush()
        account = Account(user_id=user.id, name="Current", bank_slug="yes", account_hint="1122")
        counterparty = Counterparty(user_id=user.id, display_name="Asha")
        person = Person(user_id=user.id, name="Asha")
        db.add_all([account, counterparty, person])
        db.flush()
        transaction = _transaction(
            user.id,
            account.id,
            counterparty.id,
            Purpose.PERSONAL,
            "-250.00",
            "4",
        )
        db.add(transaction)
        db.flush()
        original_description = transaction.description
        personal_group = sync_transaction_group(db, transaction)
        db.flush()

        apply_meaning_change(
            db,
            transaction=transaction,
            actor_user_id=user.id,
            purpose=Purpose.LOAN_GIVEN,
            person_id=person.id,
            employer_id=None,
            trip_id=None,
            counterparty_id=counterparty.id,
            notes="",
            reason="Confirmed this was a loan",
        )
        db.commit()

        membership = db.scalar(
            select(TransactionGroupMember).where(
                TransactionGroupMember.transaction_id == transaction.id
            )
        )
        assert membership.group_id != personal_group.id
        assert membership.group.purpose == Purpose.LOAN_GIVEN
        assert transaction.description == original_description
