import os
import subprocess
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Account, Purpose, Transaction, TransactionMeaning, User

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL migration tests",
)


def _alembic(*arguments: str) -> None:
    backend_dir = Path(__file__).resolve().parents[2]
    executable = Path(sys.executable).with_name("alembic")
    environment = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL or ""}
    subprocess.run(
        [str(executable), *arguments],
        cwd=backend_dir,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_backfill_and_rollback_preserve_movement_counts_and_sums():
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    email = f"meaning-migration-{uuid.uuid4().hex}@example.test"
    with Session(engine, expire_on_commit=False) as db:
        user = User(email=email, password_hash="unused", display_name="Migration proof")
        db.add(user)
        db.flush()
        account = Account(
            user_id=user.id,
            name="Migration account",
            bank_slug="test",
            account_hint=uuid.uuid4().hex[:8],
            opening_balance=Decimal("1000.00"),
        )
        db.add(account)
        db.flush()
        db.add_all(
            [
                Transaction(
                    user_id=user.id,
                    account_id=account.id,
                    txn_date=date(2026, 6, 1),
                    description="LEGACY ONE",
                    amount=Decimal("-25.50"),
                    dedupe_hash=uuid.uuid4().hex,
                    purpose=Purpose.PERSONAL,
                    notes="First",
                ),
                Transaction(
                    user_id=user.id,
                    account_id=account.id,
                    txn_date=date(2026, 6, 2),
                    description="LEGACY TWO",
                    amount=Decimal("100.00"),
                    dedupe_hash=uuid.uuid4().hex,
                    purpose=Purpose.INCOME,
                    notes="Second",
                ),
            ]
        )
        db.commit()
        user_id = user.id
        account_id = account.id

    def movement_snapshot() -> tuple[int, Decimal]:
        with Session(engine) as db:
            return db.execute(
                select(func.count(Transaction.id), func.sum(Transaction.amount)).where(
                    Transaction.user_id == user_id,
                    Transaction.account_id == account_id,
                )
            ).one()

    before = movement_snapshot()
    engine.dispose()
    _alembic("downgrade", "e7c31f6a9420")
    after_rollback = movement_snapshot()
    engine.dispose()
    _alembic("upgrade", "head")

    assert before == (2, Decimal("74.50"))
    assert after_rollback == before
    assert movement_snapshot() == before
    with Session(engine) as db:
        meanings = db.scalars(
            select(TransactionMeaning)
            .where(TransactionMeaning.user_id == user_id)
            .order_by(TransactionMeaning.transaction_id)
        ).all()
        assert [(meaning.purpose, meaning.notes) for meaning in meanings] == [
            (Purpose.PERSONAL, "First"),
            (Purpose.INCOME, "Second"),
        ]
