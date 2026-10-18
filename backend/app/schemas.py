from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import Purpose


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
    created_at: datetime


class TransactionUpdate(BaseModel):
    purpose: Purpose | None = None
    person_id: int | None = None
    employer_id: int | None = None
    trip_id: int | None = None
    notes: str | None = None
