from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    Account,
    ImportBatch,
    ImportRow,
    ImportStatus,
    Purpose,
    StatementImport,
    Transaction,
    TransactionMeaning,
    User,
)
from app.security import create_session


def test_private_assistant_summarizes_uploads_and_workspace_without_external_ai():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    owner = User(email="assistant@example.test", password_hash="unused", display_name="Owner")
    stranger = User(email="stranger@example.test", password_hash="unused", display_name="Other")
    db.add_all([owner, stranger])
    db.flush()
    account = Account(
        user_id=owner.id,
        name="YES current",
        bank_slug="yes",
        account_hint="9405",
        instrument_type="current",
        opening_balance=Decimal("1000.00"),
    )
    batch = ImportBatch(user_id=owner.id)
    foreign_batch = ImportBatch(user_id=stranger.id)
    db.add_all([account, batch, foreign_batch])
    db.flush()
    statement = StatementImport(
        batch_id=batch.id,
        user_id=owner.id,
        original_filename="yes.csv",
        storage_key="owner/yes.csv",
        status=ImportStatus.READY,
        bank_slug="yes",
        statement_kind="current",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        opening_balance=Decimal("1000.00"),
        closing_balance=Decimal("900.00"),
    )
    statement.rows.append(
        ImportRow(
            txn_date=date(2026, 4, 2),
            description="UPI WATER",
            amount=Decimal("-100.00"),
        )
    )
    foreign_statement = StatementImport(
        batch_id=foreign_batch.id,
        user_id=stranger.id,
        original_filename="private.csv",
        storage_key="stranger/private.csv",
        status=ImportStatus.READY,
    )
    db.add_all([statement, foreign_statement])
    db.flush()
    unresolved = Transaction(
        user_id=owner.id,
        account_id=account.id,
        txn_date=date(2026, 3, 1),
        description="UNRESOLVED",
        amount=Decimal("-150.00"),
        dedupe_hash="assistant-unresolved",
        purpose=Purpose.OTHER,
    )
    unresolved.meaning = TransactionMeaning(
        user_id=owner.id,
        purpose=Purpose.OTHER,
        version=1,
        review_status="current",
    )
    db.add(unresolved)
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            client.cookies.set("expense_session", create_session(owner.id))
            upload_reply = client.post(
                "/assistant/turn",
                json={"message": "Please read this", "import_ids": [str(statement.id)]},
            )
            assert upload_reply.status_code == 201
            assert "YES current-account statement" in upload_reply.json()["content"]
            assert "1 transaction" in upload_reply.json()["content"]
            assert "closing balance reconcile" in upload_reply.json()["content"]
            assert "1 meaning still unresolved" in upload_reply.json()["content"]
            assert upload_reply.json()["context"]["review_required"] is True

            unresolved_reply = client.post(
                "/assistant/turn", json={"message": "What is unresolved?"}
            )
            assert unresolved_reply.status_code == 201
            assert "1 confirmed ledger movement" in unresolved_reply.json()["content"]

            balance_reply = client.post(
                "/assistant/turn", json={"message": "Give me my balance summary"}
            )
            assert balance_reply.status_code == 201
            assert "₹850.00" in balance_reply.json()["content"]

            messages = client.get("/assistant/messages")
            assert messages.status_code == 200
            assert [message["role"] for message in messages.json()] == [
                "user",
                "assistant",
                "user",
                "assistant",
                "user",
                "assistant",
            ]

            foreign = client.post(
                "/assistant/turn",
                json={"message": "Read it", "import_ids": [str(foreign_statement.id)]},
            )
            assert foreign.status_code == 404

            client.cookies.set("expense_session", create_session(stranger.id))
            assert client.get("/assistant/messages").json() == []
    finally:
        app.dependency_overrides.clear()
        db.close()
