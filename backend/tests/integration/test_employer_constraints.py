import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Employer, User

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL constraint tests",
)


def test_concurrent_default_employers_leave_exactly_one_default():
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    email = f"employer-race-{uuid.uuid4().hex}@example.test"
    with Session(engine) as db:
        user = User(email=email, password_hash="unused", display_name="Constraint test")
        db.add(user)
        db.commit()
        user_id = user.id

    def create_default(name: str) -> str:
        with Session(engine) as db:
            db.add(Employer(user_id=user_id, name=name, is_default=True))
            try:
                db.commit()
                return "created"
            except IntegrityError as exc:
                db.rollback()
                assert "ux_employers_one_default_per_user" in str(exc.orig)
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(create_default, ["First", "Second"]))

    assert sorted(outcomes) == ["conflict", "created"]
    with Session(engine) as db:
        default_count = db.scalar(
            select(func.count())
            .select_from(Employer)
            .where(Employer.user_id == user_id, Employer.is_default.is_(True))
        )
        assert default_count == 1
