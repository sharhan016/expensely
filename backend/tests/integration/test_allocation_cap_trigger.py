"""The cap on allocations is enforced by PostgreSQL itself, even for raw SQL."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is required")
BEFORE = "e3b5c7d9f106"  # the revision before the trigger exists


def _alembic(*arguments: str) -> None:
    backend = Path(__file__).resolve().parents[2]
    subprocess.run(
        [str(Path(sys.executable).with_name("alembic")), *arguments],
        cwd=backend,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL or ""},
        check=True,
        capture_output=True,
        text=True,
    )


def _seed(connection) -> tuple[int, int]:
    now = "now()"
    user = connection.execute(
        text(
            "INSERT INTO users (email, password_hash, display_name, accent, created_at, "
            "updated_at) "
            f"VALUES (:email, 'x', 'Cap test', 'cobalt', {now}, {now}) RETURNING id"
        ),
        {"email": f"cap-{uuid.uuid4().hex}@example.test"},
    ).scalar_one()
    account = connection.execute(
        text(
            "INSERT INTO accounts (user_id, name, bank_slug, account_hint, kind, currency, "
            "opening_balance, created_at, updated_at) VALUES "
            f"(:u, 'Cap', 'test', :hint, 'bank', 'INR', 0, {now}, {now}) RETURNING id"
        ),
        {"u": user, "hint": uuid.uuid4().hex[:8]},
    ).scalar_one()
    transaction = connection.execute(
        text(
            "INSERT INTO transactions (user_id, account_id, txn_date, description, amount, "
            "reference, dedupe_hash, purpose, notes, created_at, updated_at) VALUES "
            f"(:u, :a, '2026-01-05', 'SYNTHETIC', -1000.00, '', :h, 'OTHER', '', {now}, {now}) "
            "RETURNING id"
        ),
        {"u": user, "a": account, "h": uuid.uuid4().hex},
    ).scalar_one()
    expectation = connection.execute(
        text(
            "INSERT INTO money_expectations (user_id, kind, status, opened_on, note, created_at, "
            f"updated_at) VALUES (:u, 'person', 'tracking', '2026-01-05', '', {now}, {now}) "
            "RETURNING id"
        ),
        {"u": user},
    ).scalar_one()
    return transaction, expectation, user


def _allocate(connection, user, expectation, transaction, amount, status="confirmed"):
    connection.execute(
        text(
            "INSERT INTO expectation_allocations (user_id, expectation_id, transaction_id, role, "
            "amount, status, decision_source, created_at, updated_at) VALUES "
            "(:u, :e, :t, 'out', :amount, :status, 'user_confirmed', now(), now())"
        ),
        {"u": user, "e": expectation, "t": transaction, "amount": amount, "status": status},
    )


def test_raw_sql_cannot_allocate_more_than_the_movement():
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        transaction, expectation, user = _seed(connection)
        _allocate(connection, user, expectation, transaction, 600)  # within the cap

    with (
        pytest.raises(DBAPIError, match="allocations exceed the movement amount"),
        engine.begin() as connection,
    ):
        _allocate(connection, user, expectation, transaction, 500)  # 1100 > 1000

    with engine.begin() as connection:
        _allocate(connection, user, expectation, transaction, 400)  # exactly the rest: allowed
        _allocate(connection, user, expectation, transaction, 900, status="reverted")  # inert

    with (
        pytest.raises(DBAPIError, match="allocations exceed the movement amount"),
        engine.begin() as connection,
    ):
        connection.execute(  # reviving an inert row past the cap is rejected as well
            text(
                "UPDATE expectation_allocations SET status = 'confirmed' "
                "WHERE transaction_id = :t AND status = 'reverted'"
            ),
            {"t": transaction},
        )
    with engine.connect() as connection:
        total = connection.execute(
            text(
                "SELECT sum(amount) FROM expectation_allocations "
                "WHERE transaction_id = :t AND status = 'confirmed'"
            ),
            {"t": transaction},
        ).scalar_one()
    assert total == 1000
    engine.dispose()


def test_the_trigger_is_created_and_dropped_by_the_migration():
    engine = create_engine(TEST_DATABASE_URL)
    query = text("SELECT count(*) FROM pg_trigger WHERE tgname = 'expectation_allocation_cap'")
    with engine.connect() as connection:
        assert connection.execute(query).scalar_one() == 1
    engine.dispose()
    _alembic("downgrade", BEFORE)
    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        assert connection.execute(query).scalar_one() == 0
        names = connection.execute(text("SELECT tablename FROM pg_tables")).scalars().all()
        assert "motion_preferences" not in names and "expectation_allocations" in names
    engine.dispose()
    _alembic("upgrade", "head")
    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        assert connection.execute(query).scalar_one() == 1
    engine.dispose()
