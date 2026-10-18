from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.meanings import apply_meaning_change, ensure_meaning
from app.models import (
    Account,
    Purpose,
    Reconciliation,
    Transaction,
    TransactionMeaningChange,
    User,
)


def test_finalized_meaning_correction_is_audited_without_changing_bank_evidence():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="audit@example.test", password_hash="unused", display_name="Audit")
        db.add(user)
        db.flush()
        account = Account(
            user_id=user.id,
            name="Audit account",
            bank_slug="test",
            account_hint="9001",
        )
        db.add(account)
        db.flush()
        transaction = Transaction(
            user_id=user.id,
            account_id=account.id,
            txn_date=date(2026, 4, 2),
            description="ORIGINAL BANK DESCRIPTION",
            amount=Decimal("-42.50"),
            reference="BANK-REFERENCE",
            dedupe_hash="audit-movement",
        )
        reconciliation = Reconciliation(
            user_id=user.id,
            account_id=account.id,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
            opening_balance=Decimal("100.00"),
            statement_closing_balance=Decimal("57.50"),
            status="balanced",
            finalized_at=datetime.now(UTC),
        )
        db.add_all([transaction, reconciliation])
        db.flush()
        meaning = ensure_meaning(db, transaction)

        with pytest.raises(HTTPException) as exc_info:
            apply_meaning_change(
                db,
                transaction=transaction,
                actor_user_id=user.id,
                purpose=Purpose.PERSONAL,
                person_id=None,
                employer_id=None,
                trip_id=None,
                notes="Corrected",
                expected_version=1,
            )
        assert exc_info.value.status_code == 422

        apply_meaning_change(
            db,
            transaction=transaction,
            actor_user_id=user.id,
            purpose=Purpose.PERSONAL,
            person_id=None,
            employer_id=None,
            trip_id=None,
            notes="Corrected",
            expected_version=1,
            reason="Receipt confirms this was personal",
        )
        db.commit()

        assert meaning.version == 2
        assert meaning.review_status == "needs_review"
        assert reconciliation.status == "balanced"
        assert reconciliation.classification_status == "needs_review"
        assert transaction.txn_date == date(2026, 4, 2)
        assert transaction.description == "ORIGINAL BANK DESCRIPTION"
        assert transaction.amount == Decimal("-42.50")
        assert transaction.reference == "BANK-REFERENCE"
        change = db.scalar(select(TransactionMeaningChange))
        assert change.actor_user_id == user.id
        assert change.before_values["purpose"] == "other"
        assert change.after_values["purpose"] == "personal"
        assert change.reason == "Receipt confirms this was personal"

        with pytest.raises(HTTPException) as stale:
            apply_meaning_change(
                db,
                transaction=transaction,
                actor_user_id=user.id,
                purpose=Purpose.INCOME,
                person_id=None,
                employer_id=None,
                trip_id=None,
                notes="Stale edit",
                expected_version=1,
                reason="Attempted stale correction",
            )
        assert stale.value.status_code == 409


def test_noop_meaning_update_does_not_create_audit_noise():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="noop@example.test", password_hash="unused", display_name="Noop")
        db.add(user)
        db.flush()
        account = Account(
            user_id=user.id,
            name="Noop account",
            bank_slug="test",
            account_hint="9002",
        )
        db.add(account)
        db.flush()
        transaction = Transaction(
            user_id=user.id,
            account_id=account.id,
            txn_date=date(2026, 5, 1),
            description="UNCHANGED",
            amount=Decimal("-1.00"),
            dedupe_hash="noop-movement",
        )
        db.add(transaction)
        db.flush()
        meaning = ensure_meaning(db, transaction)

        apply_meaning_change(
            db,
            transaction=transaction,
            actor_user_id=user.id,
            purpose=Purpose.OTHER,
            person_id=None,
            employer_id=None,
            trip_id=None,
            notes="",
            expected_version=1,
        )
        db.flush()

        assert meaning.version == 1
        assert db.scalar(select(TransactionMeaningChange)) is None
