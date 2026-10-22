import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is required")
BEFORE = "d1a7b3c5e902"


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


def _tables() -> set[str]:
    engine = create_engine(TEST_DATABASE_URL)
    names = set(inspect(engine).get_table_names())
    engine.dispose()
    return names


def test_money_in_motion_tables_migrate_up_down_up():
    _alembic("upgrade", "head")
    assert {"money_expectations", "expectation_allocations"} <= _tables()

    _alembic("downgrade", BEFORE)
    names = _tables()
    assert not {"money_expectations", "expectation_allocations"} & names
    assert {"workflow_runs", "transactions"} <= names  # earlier tables untouched

    _alembic("upgrade", "head")
    assert {"money_expectations", "expectation_allocations"} <= _tables()
