import json
import logging
import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.imports import _stage_parsed
from app.main import app
from app.models import Account, ImportBatch, StatementImport, User
from app.parsers.types import ParsedStatement, ParsedTransaction
from app.security import create_session


class TrustWorkspace:
    """Synthetic single-owner workspace plus a stranger, shared by trust tests."""

    def __init__(self, db: Session, client: TestClient, owner: User, stranger: User):
        self.db = db
        self.client = client
        self.owner = owner
        self.stranger = stranger

    def account(self, hint: str = "1111", *, user: User | None = None, **kwargs) -> Account:
        account = Account(
            user_id=(user or self.owner).id,
            name=f"Account {hint}",
            bank_slug="test",
            account_hint=hint,
            instrument_type="current",
            **kwargs,
        )
        self.db.add(account)
        self.db.commit()
        return account

    def stage(
        self,
        rows: list[tuple[date, str, str, str]],
        *,
        hint: str = "1111",
        opening: str | None = None,
        closing: str | None = None,
        period: tuple[date, date] | None = None,
        user: User | None = None,
    ) -> StatementImport:
        """Stage rows as (date, description, amount, reference) through the real pipeline."""
        owner = user or self.owner
        batch = ImportBatch(user_id=owner.id)
        self.db.add(batch)
        self.db.flush()
        statement = StatementImport(
            batch_id=batch.id,
            user_id=owner.id,
            original_filename="synthetic.csv",
            storage_key=f"{owner.id}/synthetic.csv",
        )
        self.db.add(statement)
        self.db.flush()
        parsed = ParsedStatement(
            transactions=[
                ParsedTransaction(
                    txn_date=txn_date,
                    description=description,
                    amount=Decimal(amount),
                    reference=reference,
                )
                for txn_date, description, amount, reference in rows
            ],
            bank_slug="test",
            source_hint=hint,
            statement_kind="current",
            instrument_confidence="high",
            period_start=period[0] if period else min(row[0] for row in rows),
            period_end=period[1] if period else max(row[0] for row in rows),
            opening_balance=Decimal(opening) if opening is not None else None,
            closing_balance=Decimal(closing) if closing is not None else None,
        )
        _stage_parsed(self.db, statement, parsed, owner.id)
        self.db.commit()
        return statement

    def confirm(self, statement: StatementImport, account: Account | None = None, **extra):
        body = {"account_id": account.id} if account else {}
        return self.client.post(f"/imports/{statement.id}/confirm", json={**body, **extra})


def _test_engine():
    """SQLite by default; set UNIT_DATABASE_URL to run the same tests on PostgreSQL.

    Use a database separate from the migration-test one: this fixture drops and recreates tables.
    """
    url = os.getenv("UNIT_DATABASE_URL")
    if url:
        engine = create_engine(url, pool_pre_ping=True)
        Base.metadata.drop_all(engine)
        return engine
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


@pytest.fixture
def workspace() -> Iterator[TrustWorkspace]:
    engine = _test_engine()
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    owner = User(email="owner@example.test", password_hash="unused", display_name="Owner")
    stranger = User(email="stranger@example.test", password_hash="unused", display_name="Other")
    db.add_all([owner, stranger])
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.cookies.set("expense_session", create_session(owner.id))
        yield TrustWorkspace(db, client, owner, stranger)
    app.dependency_overrides.clear()
    db.close()
    if os.getenv("UNIT_DATABASE_URL"):
        Base.metadata.drop_all(engine)
    engine.dispose()


class PrivacyProbe:
    """Collects everything a person debugging the system could later read, then checks it.

    Captured: every log record (all loggers, any level) and any diagnostic payload a test hands
    over with `record` (error bodies, stored error messages, future workflow events).
    Successful business responses are not diagnostics, so they are not recorded by default.
    """

    def __init__(self, caplog: pytest.LogCaptureFixture):
        self._caplog = caplog
        self._payloads: list[tuple[str, str]] = []

    def record(self, label: str, payload) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        self._payloads.append((label, text))

    def record_response(self, label: str, response) -> None:
        """Record only failures; a 2xx body is the product's answer to its owner."""
        if response.status_code >= 400:
            self.record(label, response.text)

    def diagnostics(self) -> str:
        records = [
            f"{record.name}|{record.getMessage()}|{record.exc_text or ''}"
            for record in self._caplog.records
        ]
        return "\n".join(records + [f"{label}|{text}" for label, text in self._payloads])

    def assert_clean(self, *secrets: str) -> None:
        text = self.diagnostics()
        leaked = [secret for secret in secrets if secret and secret in text]
        assert not leaked, f"{len(leaked)} sensitive value(s) appeared in diagnostics"


@pytest.fixture
def privacy_probe(caplog: pytest.LogCaptureFixture) -> PrivacyProbe:
    caplog.set_level(logging.DEBUG)
    return PrivacyProbe(caplog)
