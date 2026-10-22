import os
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, select

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL migration tests",
)
PREVIOUS = "f6bc8d2e475"


def _alembic(*arguments: str) -> None:
    backend_dir = Path(__file__).resolve().parents[2]
    executable = Path(sys.executable).with_name("alembic")
    subprocess.run(
        [str(executable), *arguments],
        cwd=backend_dir,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL or ""},
        check=True,
        capture_output=True,
        text=True,
    )


def _tables(*names):
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    metadata = MetaData()
    return engine, [Table(name, metadata, autoload_with=engine) for name in names]


def test_coverage_migration_preserves_data_defaults_new_columns_and_rolls_back():
    _alembic("downgrade", PREVIOUS)
    engine, (users, accounts, reconciliations) = _tables("users", "accounts", "reconciliations")
    now = datetime.now(UTC)
    with engine.begin() as connection:
        user_id = connection.execute(
            users.insert()
            .values(
                email=f"coverage-migration-{uuid.uuid4().hex}@example.test",
                password_hash="unused",
                display_name="Coverage migration",
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
                name="Legacy account",
                bank_slug="yes",
                account_hint="1111",
                kind="current",
                currency="INR",
                opening_balance=Decimal("100.00"),
                created_at=now,
                updated_at=now,
            )
            .returning(accounts.c.id)
        ).scalar_one()
        reconciliation_id = connection.execute(
            reconciliations.insert()
            .values(
                user_id=user_id,
                account_id=account_id,
                period_start=date(2026, 1, 1),
                period_end=date(2026, 1, 31),
                opening_balance=Decimal("100.00"),
                statement_closing_balance=Decimal("90.00"),
                status="balanced",
                classification_status="current",
                created_at=now,
                updated_at=now,
            )
            .returning(reconciliations.c.id)
        ).scalar_one()
    engine.dispose()

    _alembic("upgrade", "head")
    engine, (accounts, reconciliations) = _tables("accounts", "reconciliations")
    with engine.connect() as connection:
        account = connection.execute(select(accounts).where(accounts.c.id == account_id)).one()
        reconciliation = connection.execute(
            select(reconciliations).where(reconciliations.c.id == reconciliation_id)
        ).one()
    assert account.in_scope is True
    assert account.records_start is None and account.records_opening_balance is None
    assert account.opening_balance == Decimal("100.00")
    # Existing rows stay as they were and are treated as unverified until recomputed.
    assert reconciliation.status == "balanced"
    assert reconciliation.movement_fingerprint is None
    assert reconciliation.source_statement_id is None
    assert reconciliation.invalidated_at is None
    assert {"coverage_gap_unbridges", "cash_withdrawal_markers", "cash_statement_entries"} <= set(
        inspect(engine).get_table_names()
    )
    engine.dispose()

    _alembic("downgrade", PREVIOUS)
    engine, (accounts, reconciliations) = _tables("accounts", "reconciliations")
    with engine.connect() as connection:
        assert connection.scalar(
            select(accounts.c.opening_balance).where(accounts.c.id == account_id)
        ) == Decimal("100.00")
        assert (
            connection.scalar(
                select(reconciliations.c.status).where(reconciliations.c.id == reconciliation_id)
            )
            == "balanced"
        )
    assert "in_scope" not in {column["name"] for column in inspect(engine).get_columns("accounts")}
    assert "cash_withdrawal_markers" not in inspect(engine).get_table_names()
    engine.dispose()

    _alembic("upgrade", "head")
