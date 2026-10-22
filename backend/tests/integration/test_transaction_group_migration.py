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

from app.models import (
    Account,
    Counterparty,
    Purpose,
    Transaction,
    TransactionGroup,
    TransactionGroupMember,
    TransactionMeaning,
    User,
)

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


def test_transaction_group_migration_backfills_and_rolls_back_without_movement_changes():
    _alembic("downgrade", "b2d8f4a6c031")
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    email = f"group-migration-{uuid.uuid4().hex}@example.test"
    with Session(engine, expire_on_commit=False) as db:
        user = User(email=email, password_hash="unused", display_name="Group migration")
        db.add(user)
        db.flush()
        account = Account(
            user_id=user.id,
            name="Current",
            bank_slug="yes",
            account_hint=uuid.uuid4().hex[:8],
        )
        counterparty = Counterparty(user_id=user.id, display_name="Asha")
        db.add_all([account, counterparty])
        db.flush()
        movements = []
        for day, amount in ((1, "-100.00"), (2, "-200.00")):
            transaction = Transaction(
                user_id=user.id,
                account_id=account.id,
                txn_date=date(2026, 7, day),
                description=f"ASHA PAYMENT {day}",
                amount=Decimal(amount),
                dedupe_hash=uuid.uuid4().hex,
                purpose=Purpose.PERSONAL,
            )
            transaction.meaning = TransactionMeaning(
                user_id=user.id,
                purpose=Purpose.PERSONAL,
                counterparty_id=counterparty.id,
                version=1,
                review_status="current",
            )
            movements.append(transaction)
        db.add_all(movements)
        db.commit()
        user_id = user.id

    def movement_snapshot() -> tuple[int, Decimal]:
        with Session(engine) as db:
            return db.execute(
                select(func.count(Transaction.id), func.sum(Transaction.amount)).where(
                    Transaction.user_id == user_id
                )
            ).one()

    before = movement_snapshot()
    engine.dispose()
    _alembic("upgrade", "head")

    with Session(engine) as db:
        groups = db.scalars(
            select(TransactionGroup).where(TransactionGroup.user_id == user_id)
        ).all()
        members = db.scalar(
            select(func.count(TransactionGroupMember.id)).where(
                TransactionGroupMember.user_id == user_id
            )
        )
        assert len(groups) == 1
        assert groups[0].purpose == Purpose.PERSONAL
        assert groups[0].direction == "paid"
        assert members == 2

    engine.dispose()
    _alembic("downgrade", "b2d8f4a6c031")
    assert movement_snapshot() == before == (2, Decimal("-300.00"))
    engine.dispose()
    _alembic("upgrade", "head")
    assert movement_snapshot() == before
