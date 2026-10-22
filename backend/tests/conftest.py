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


@pytest.fixture
def workspace() -> Iterator[TrustWorkspace]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
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
