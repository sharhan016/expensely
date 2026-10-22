import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.exc import DataError

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


def test_account_instrument_migration_preserves_valid_types_and_rejects_arbitrary_values():
    previous_revision = "e5ab7c9d364"
    _alembic("downgrade", previous_revision)
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    metadata = MetaData()
    users = Table("users", metadata, autoload_with=engine)
    accounts = Table("accounts", metadata, autoload_with=engine)
    now = datetime.now(UTC)
    account_hint = uuid.uuid4().hex[:8]

    with engine.begin() as connection:
        user_id = connection.execute(
            users.insert()
            .values(
                email=f"instrument-migration-{uuid.uuid4().hex}@example.test",
                password_hash="unused",
                display_name="Instrument migration",
                accent="coral",
                created_at=now,
                updated_at=now,
            )
            .returning(users.c.id)
        ).scalar_one()
        account_id = connection.execute(
            accounts.insert()
            .values(
                user_id=user_id,
                name="YES current",
                bank_slug="yes",
                account_hint=account_hint,
                kind="current",
                currency="INR",
                opening_balance=Decimal("1000.00"),
                created_at=now,
                updated_at=now,
            )
            .returning(accounts.c.id)
        ).scalar_one()

    engine.dispose()
    _alembic("upgrade", "head")
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    accounts = Table("accounts", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        assert connection.scalar(select(accounts.c.kind).where(accounts.c.id == account_id)) == (
            "current"
        )

    with pytest.raises(DataError), engine.begin() as connection:
        connection.execute(
            accounts.insert().values(
                user_id=user_id,
                name="Invalid instrument",
                bank_slug="yes",
                account_hint=uuid.uuid4().hex[:8],
                kind="wallet",
                currency="INR",
                opening_balance=Decimal("0.00"),
                created_at=now,
                updated_at=now,
            )
        )

    engine.dispose()
    _alembic("downgrade", previous_revision)
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    accounts = Table("accounts", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        assert connection.scalar(select(accounts.c.kind).where(accounts.c.id == account_id)) == (
            "current"
        )

    engine.dispose()
    _alembic("upgrade", "head")
