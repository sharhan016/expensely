import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import MetaData, Table, create_engine, select

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


def test_card_statement_amount_migration_preserves_legacy_amount_on_rollback():
    previous_revision = "d4fa6b8c253"
    _alembic("downgrade", previous_revision)
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    metadata = MetaData()
    users = Table("users", metadata, autoload_with=engine)
    batches = Table("import_batches", metadata, autoload_with=engine)
    statements = Table("statement_imports", metadata, autoload_with=engine)
    batch_id = uuid.uuid4()
    statement_id = uuid.uuid4()
    now = datetime.now(UTC)

    with engine.begin() as connection:
        user_id = connection.execute(
            users.insert()
            .values(
                email=f"card-amount-migration-{uuid.uuid4().hex}@example.test",
                password_hash="unused",
                display_name="Card migration",
                accent="coral",
                created_at=now,
                updated_at=now,
            )
            .returning(users.c.id)
        ).scalar_one()
        connection.execute(
            batches.insert().values(
                id=batch_id,
                user_id=user_id,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            statements.insert().values(
                id=statement_id,
                batch_id=batch_id,
                user_id=user_id,
                original_filename="legacy-card.pdf",
                storage_key="migration-test/legacy-card.pdf",
                status="READY",
                bank_slug="yes",
                statement_kind="credit_card",
                closing_balance=Decimal("-500.00"),
                reconciliation_status="matched",
                reconciliation_difference=Decimal("0.00"),
                error_message="",
                created_at=now,
                updated_at=now,
            )
        )

    engine.dispose()
    _alembic("upgrade", "head")
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    statements = Table("statement_imports", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        upgraded = connection.execute(
            select(
                statements.c.closing_balance,
                statements.c.total_amount_due,
                statements.c.reconciliation_status,
                statements.c.reconciliation_difference,
            ).where(statements.c.id == statement_id)
        ).one()
    assert upgraded == (None, Decimal("500.00"), "unavailable", None)

    engine.dispose()
    _alembic("downgrade", previous_revision)
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    statements = Table("statement_imports", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        restored = connection.scalar(
            select(statements.c.closing_balance).where(statements.c.id == statement_id)
        )
    assert restored == Decimal("-500.00")

    engine.dispose()
    _alembic("upgrade", "head")
