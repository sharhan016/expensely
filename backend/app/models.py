from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
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
    detected_account_name: Mapped[str] = mapped_column(String(120), default="")
    account_hint: Mapped[str] = mapped_column(String(32), default="")
    statement_kind: Mapped[str] = mapped_column(String(24), default="bank")
    instrument_confidence: Mapped[str] = mapped_column(String(16), default="unknown")
    parser_name: Mapped[str] = mapped_column(String(80), default="generic_table")
    parser_version: Mapped[str] = mapped_column(String(24), default="1")
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    opening_balance: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    closing_balance: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    reconciliation_status: Mapped[str] = mapped_column(String(20), default="unavailable")
    reconciliation_difference: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
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
    counterparty_id: Mapped[int | None] = mapped_column(
        ForeignKey("counterparties.id", ondelete="SET NULL"), nullable=True
    )
    counterparty_name: Mapped[str] = mapped_column(String(160), default="")
    counterparty_alias_type: Mapped[str] = mapped_column(String(32), default="")
    counterparty_alias_value: Mapped[str] = mapped_column(String(180), default="")
    counterparty_confidence: Mapped[str] = mapped_column(String(16), default="unresolved")
    confidence: Mapped[str] = mapped_column(String(16), default="unresolved")
    category: Mapped[str] = mapped_column(String(40), default="uncategorized")
    category_confidence: Mapped[str] = mapped_column(String(16), default="unresolved")
    classification_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    decision_source: Mapped[str] = mapped_column(String(32), default="unresolved")
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


class Counterparty(TimestampMixin, Base):
    __tablename__ = "counterparties"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(24), default="unknown")
    default_purpose: Mapped[Purpose | None] = mapped_column(Enum(Purpose), nullable=True)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    aliases: Mapped[list[CounterpartyAlias]] = relationship(
        back_populates="counterparty", cascade="all, delete-orphan"
    )


class CounterpartyAlias(TimestampMixin, Base):
    __tablename__ = "counterparty_aliases"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "alias_type", "alias_value", name="uq_counterparty_alias_owner_type_value"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    counterparty_id: Mapped[int] = mapped_column(
        ForeignKey("counterparties.id", ondelete="CASCADE"), index=True
    )
    alias_type: Mapped[str] = mapped_column(String(32))
    alias_value: Mapped[str] = mapped_column(String(180))
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    counterparty: Mapped[Counterparty] = relationship(back_populates="aliases")


class Employer(TimestampMixin, Base):
    __tablename__ = "employers"
    __table_args__ = (
        Index(
            "ux_employers_one_default_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_default"),
            sqlite_where=text("is_default"),
        ),
    )
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
    purchase_window_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    purchase_window_end: Mapped[date | None] = mapped_column(Date, nullable=True)


class TripTransactionExclusion(TimestampMixin, Base):
    __tablename__ = "trip_transaction_exclusions"
    __table_args__ = (UniqueConstraint("trip_id", "transaction_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )


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
    meaning: Mapped[TransactionMeaning | None] = relationship(
        back_populates="transaction", cascade="all, delete-orphan", uselist=False
    )

    @property
    def meaning_version(self) -> int:
        return self.meaning.version if self.meaning else 1


class TransactionMeaning(TimestampMixin, Base):
    __tablename__ = "transaction_meanings"
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[Purpose] = mapped_column(Enum(Purpose), default=Purpose.OTHER)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"), nullable=True)
    employer_id: Mapped[int | None] = mapped_column(ForeignKey("employers.id"), nullable=True)
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id"), nullable=True)
    counterparty_id: Mapped[int | None] = mapped_column(
        ForeignKey("counterparties.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(40), default="uncategorized")
    category_confidence: Mapped[str] = mapped_column(String(16), default="unresolved")
    classification_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    decision_source: Mapped[str] = mapped_column(String(32), default="unresolved")
    notes: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(default=1)
    review_status: Mapped[str] = mapped_column(String(20), default="current")
    transaction: Mapped[Transaction] = relationship(back_populates="meaning")
    counterparty: Mapped[Counterparty | None] = relationship()


class TransactionMeaningChange(Base):
    __tablename__ = "transaction_meaning_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    version: Mapped[int]
    before_values: Mapped[dict] = mapped_column(JSON)
    after_values: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TransactionLink(TimestampMixin, Base):
    __tablename__ = "transaction_links"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "outflow_transaction_id",
            "inflow_transaction_id",
            name="uq_transaction_link_owner_pair",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    link_type: Mapped[str] = mapped_column(String(32))
    outflow_transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    inflow_transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="confirmed")
    confidence: Mapped[str] = mapped_column(String(16), default="high")
    decision_source: Mapped[str] = mapped_column(String(32), default="automatic_rule")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    outflow_transaction: Mapped[Transaction] = relationship(foreign_keys=[outflow_transaction_id])
    inflow_transaction: Mapped[Transaction] = relationship(foreign_keys=[inflow_transaction_id])


class TransactionGroup(TimestampMixin, Base):
    __tablename__ = "transaction_groups"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "counterparty_id",
            "purpose",
            "direction",
            name="uq_transaction_group_owner_meaning_direction",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    counterparty_id: Mapped[int] = mapped_column(
        ForeignKey("counterparties.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[Purpose] = mapped_column(Enum(Purpose))
    direction: Mapped[str] = mapped_column(String(16))
    group_key: Mapped[str] = mapped_column(String(160), unique=True)
    members: Mapped[list[TransactionGroupMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class TransactionGroupMember(Base):
    __tablename__ = "transaction_group_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("transaction_groups.id", ondelete="CASCADE"), index=True
    )
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), unique=True, index=True
    )
    group: Mapped[TransactionGroup] = relationship(back_populates="members")
    transaction: Mapped[Transaction] = relationship()


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


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
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    classification_status: Mapped[str] = mapped_column(String(20), default="current")


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
