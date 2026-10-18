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
    notes: str
    meaning_version: int = 1
    created_at: datetime


class TransactionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Purpose | None = None
    person_id: int | None = None
    employer_id: int | None = None
    trip_id: int | None = None
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
    confidence: str
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
    period_start: date | None
    period_end: date | None
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    overlap_warning: bool
    error_message: str
    rows: list[ImportRowResponse] = []


class ImportRowUpdate(BaseModel):
    purpose: Purpose | None = None
    selected: bool | None = None
    person_id: int | None = None
    employer_id: int | None = None
    trip_id: int | None = None


class ImportBulkUpdate(BaseModel):
    row_ids: list[int]
    purpose: Purpose | None = None
    selected: bool | None = None
    person_id: int | None = None
    employer_id: int | None = None
    trip_id: int | None = None


class ImportConfirmRequest(BaseModel):
    account_id: int | None = None
    account_name: str | None = Field(default=None, max_length=120)
    bank_slug: str | None = Field(
        default=None, min_length=2, max_length=40, pattern=r"^[a-z0-9_-]+$"
    )


class ImportConfirmResponse(BaseModel):
    imported: int
    skipped_duplicates: int
    account_id: int


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
