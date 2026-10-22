from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr

from app.models import ImportStatus, Purpose


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(ORMModel):
    id: int
    email: str
    display_name: str
    accent: str


class ThemeUpdate(BaseModel):
    accent: str


class AccountResponse(ORMModel):
    id: int
    name: str
    bank_slug: str
    account_hint: str
    kind: str
    currency: str
    opening_balance: Decimal
    current_balance: Decimal = Decimal("0")
    statement_count: int = 0


class AccountUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    bank_slug: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9_-]+$")


class PersonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    relationship_label: str = Field(default="", max_length=80)
    notes: str = ""


class PersonResponse(PersonCreate, ORMModel):
    id: int
    entrusted_balance: Decimal = Decimal("0")
    loan_balance: Decimal = Decimal("0")


class EmployerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    is_default: bool = False


class EmployerResponse(EmployerCreate, ORMModel):
    id: int


class TransactionResponse(ORMModel):
    id: int
    account_id: int
    txn_date: date
    description: str
    amount: Decimal
    bank_balance: Decimal | None
    purpose: Purpose
    person_id: int | None
    employer_id: int | None
    trip_id: int | None
    counterparty_id: int | None = None
    counterparty_name: str = ""
    category: str = "uncategorized"
    category_confidence: str = "unresolved"
    classification_evidence: dict = Field(default_factory=dict)
    decision_source: str = "unresolved"
    notes: str
    meaning_version: int = 1
    created_at: datetime


class TransactionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Purpose | None = None
    person_id: int | None = None
    employer_id: int | None = None
    trip_id: int | None = None
    counterparty_id: int | None = None
    category: str | None = Field(default=None, min_length=2, max_length=40)
    notes: str | None = None
    expected_version: int | None = Field(default=None, ge=1)
    reason: str = Field(default="", max_length=500)


class TransactionMeaningChangeResponse(ORMModel):
    id: int
    version: int
    before_values: dict[str, str | int | None]
    after_values: dict[str, str | int | None]
    reason: str
    created_at: datetime


class ImportRowResponse(ORMModel):
    id: int
    txn_date: date
    description: str
    amount: Decimal
    balance: Decimal | None
    reference: str
    purpose: Purpose
    person_id: int | None
    employer_id: int | None
    trip_id: int | None
    counterparty_id: int | None
    counterparty_name: str
    counterparty_alias_type: str
    counterparty_alias_value: str
    counterparty_confidence: str
    confidence: str
    category: str
    category_confidence: str
    classification_evidence: dict
    decision_source: str
    duplicate_of_id: int | None
    selected: bool


class StatementImportResponse(ORMModel):
    id: UUID
    batch_id: UUID
    account_id: int | None
    original_filename: str
    status: ImportStatus
    bank_slug: str
    detected_account_name: str
    account_hint: str
    statement_kind: str
    instrument_confidence: str
    parser_name: str
    parser_version: str
    period_start: date | None
    period_end: date | None
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    reconciliation_status: str
    reconciliation_difference: Decimal | None
    overlap_warning: bool
    error_message: str
    rows: list[ImportRowResponse] = []


class ImportRowUpdate(BaseModel):
    purpose: Purpose | None = None
    selected: bool | None = None
    person_id: int | None = None
    employer_id: int | None = None
    trip_id: int | None = None
    counterparty_id: int | None = None
    category: str | None = Field(default=None, min_length=2, max_length=40)


class ImportBulkUpdate(BaseModel):
    row_ids: list[int]
    purpose: Purpose | None = None
    selected: bool | None = None
    person_id: int | None = None
    employer_id: int | None = None
    trip_id: int | None = None
    counterparty_id: int | None = None
    category: str | None = Field(default=None, min_length=2, max_length=40)


class ImportConfirmRequest(BaseModel):
    account_id: int | None = None
    account_name: str | None = Field(default=None, max_length=120)
    statement_kind: str | None = Field(
        default=None, pattern=r"^(bank|current|savings|credit_card)$"
    )
    bank_slug: str | None = Field(
        default=None, min_length=2, max_length=40, pattern=r"^[a-z0-9_-]+$"
    )
    allow_reconciliation_difference: bool = False


class ImportConfirmResponse(BaseModel):
    imported: int
    skipped_duplicates: int
    account_id: int
    linked_card_payments: int = 0
    linked_refunds: int = 0


class PdfPasswordRequest(BaseModel):
    password: SecretStr


class TripCreate(BaseModel):
    name: str = Field(min_length=1, max_length=140)
    starts_on: date | None = None
    ends_on: date | None = None
    purchase_window_start: date | None = None
    purchase_window_end: date | None = None


class TripTransactionDecision(BaseModel):
    status: str = Field(pattern="^(included|excluded|pending)$")


class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=140)
    description_contains: str = Field(min_length=2, max_length=180)
    purpose: Purpose
    person_id: int | None = None
    employer_id: int | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=140)
    description_contains: str | None = Field(default=None, min_length=2, max_length=180)
    purpose: Purpose | None = None
    person_id: int | None = None
    employer_id: int | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    is_active: bool | None = None


class ReconciliationCreate(BaseModel):
    account_id: int
    period_start: date
    period_end: date
    opening_balance: Decimal
    statement_closing_balance: Decimal


class CounterpartyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    kind: str | None = Field(default=None, pattern=r"^(unknown|person|merchant|organization)$")
    default_purpose: Purpose | None = None


class CounterpartySummary(ORMModel):
    id: int
    display_name: str
    kind: str
    default_purpose: Purpose | None
    is_confirmed: bool
    paid_total: Decimal = Decimal("0")
    received_total: Decimal = Decimal("0")
    transaction_count: int = 0
    unresolved_count: int = 0


class CounterpartyPurposeTotal(BaseModel):
    purpose: Purpose
    paid: Decimal
    received: Decimal
    transaction_count: int


class CounterpartyTransaction(BaseModel):
    id: int
    txn_date: date
    description: str
    amount: Decimal
    purpose: Purpose


class CounterpartyDetail(BaseModel):
    counterparty: CounterpartySummary
    purpose_totals: list[CounterpartyPurposeTotal]
    transactions: list[CounterpartyTransaction]


class TransactionGroupSummary(BaseModel):
    id: int
    counterparty_id: int
    counterparty_name: str
    purpose: Purpose
    direction: str
    total: Decimal
    transaction_count: int


class TransactionGroupDetail(BaseModel):
    group: TransactionGroupSummary
    transactions: list[CounterpartyTransaction]


class CardPaymentLinkResponse(BaseModel):
    id: int
    status: str
    confidence: str
    decision_source: str
    amount: Decimal
    outflow_transaction_id: int
    inflow_transaction_id: int
    bank_account_name: str
    card_account_name: str
    bank_date: date
    card_date: date


class UnmatchedCardPaymentResponse(BaseModel):
    transaction_id: int
    amount: Decimal
    txn_date: date
    card_account_name: str
    reason: str


class CardRefundLinkResponse(BaseModel):
    id: int
    link_type: str
    status: str
    confidence: str
    amount: Decimal
    purchase_transaction_id: int
    refund_transaction_id: int
    card_account_name: str
    purchase_date: date
    refund_date: date


class TransactionLinkDecision(BaseModel):
    status: str = Field(pattern=r"^(rejected)$")


class AssistantTurnRequest(BaseModel):
    message: str = Field(default="", max_length=4000)
    import_ids: list[UUID] = Field(default_factory=list, max_length=20)


class AssistantMessageResponse(ORMModel):
    id: int
    role: str
    content: str
    context: dict
    created_at: datetime
