from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Purpose(enum.StrEnum):
    PERSONAL = "personal"
    BUSINESS = "business"
    INCOME = "income"
    TRANSFER = "transfer"
    LOAN_GIVEN = "loan_given"
    LOAN_REPAYMENT = "loan_repayment"
    ENTRUSTED = "entrusted"
    REIMBURSEMENT = "reimbursement"
    REFUND = "refund"
    OTHER = "other"


class ImportStatus(enum.StrEnum):
    UPLOADED = "uploaded"
    PASSWORD_REQUIRED = "password_required"
    PROCESSING = "processing"
    READY = "ready_for_review"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    accent: Mapped[str] = mapped_column(String(20), default="cobalt")
    accounts: Mapped[list[Account]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("user_id", "bank_slug", "account_hint"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    bank_slug: Mapped[str] = mapped_column(String(40))
    account_hint: Mapped[str] = mapped_column(String(32), default="")
    kind: Mapped[str] = mapped_column(String(24), default="bank")
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    user: Mapped[User] = relationship(back_populates="accounts")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="account")


class ImportBatch(TimestampMixin, Base):
    __tablename__ = "import_batches"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    files: Mapped[list[StatementImport]] = relationship(cascade="all, delete-orphan")


class StatementImport(TimestampMixin, Base):
    __tablename__ = "statement_imports"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[ImportStatus] = mapped_column(Enum(ImportStatus), default=ImportStatus.UPLOADED)
    bank_slug: Mapped[str] = mapped_column(String(40), default="")
    account_hint: Mapped[str] = mapped_column(String(32), default="")
    statement_kind: Mapped[str] = mapped_column(String(24), default="bank")
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    opening_balance: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    closing_balance: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    overlap_warning: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str] = mapped_column(Text, default="")
    rows: Mapped[list[ImportRow]] = relationship(cascade="all, delete-orphan")


class ImportRow(Base):
    __tablename__ = "import_rows"
    id: Mapped[int] = mapped_column(primary_key=True)
    statement_import_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("statement_imports.id", ondelete="CASCADE"), index=True
    )
    txn_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    balance: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    reference: Mapped[str] = mapped_column(String(120), default="")
    purpose: Mapped[Purpose] = mapped_column(Enum(Purpose), default=Purpose.OTHER)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"), nullable=True)
    employer_id: Mapped[int | None] = mapped_column(ForeignKey("employers.id"), nullable=True)
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id"), nullable=True)
    confidence: Mapped[str] = mapped_column(String(16), default="unresolved")
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id"), nullable=True
    )
    selected: Mapped[bool] = mapped_column(Boolean, default=True)


class Person(TimestampMixin, Base):
    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("user_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    relationship_label: Mapped[str] = mapped_column(String(80), default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class Employer(TimestampMixin, Base):
    __tablename__ = "employers"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class Trip(TimestampMixin, Base):
    __tablename__ = "trips"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(140))
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class Transaction(TimestampMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_hash"),
        Index("ix_transactions_user_date", "user_id", "txn_date"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    statement_import_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("statement_imports.id"), nullable=True
    )
    txn_date: Mapped[date] = mapped_column(Date)
    value_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    bank_balance: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    reference: Mapped[str] = mapped_column(String(120), default="")
    dedupe_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[Purpose] = mapped_column(Enum(Purpose), default=Purpose.OTHER)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"), nullable=True)
    employer_id: Mapped[int | None] = mapped_column(ForeignKey("employers.id"), nullable=True)
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id"), nullable=True)
    reimbursement_for_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id"), nullable=True
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    account: Mapped[Account] = relationship(back_populates="transactions")


class Reconciliation(TimestampMixin, Base):
    __tablename__ = "reconciliations"
    __table_args__ = (UniqueConstraint("user_id", "account_id", "period_start", "period_end"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    statement_closing_balance: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    status: Mapped[str] = mapped_column(String(20), default="draft")


class ClassificationRule(TimestampMixin, Base):
    __tablename__ = "classification_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(140))
    description_contains: Mapped[str] = mapped_column(String(180))
    purpose: Mapped[Purpose] = mapped_column(Enum(Purpose))
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"), nullable=True)
    employer_id: Mapped[int | None] = mapped_column(ForeignKey("employers.id"), nullable=True)
    min_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    max_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
