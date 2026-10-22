from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class ParsedTransaction:
    txn_date: date
    description: str
    amount: Decimal
    balance: Decimal | None = None
    reference: str = ""
    category: str = "uncategorized"
    category_confidence: str = "unresolved"
    classification_evidence: dict[str, str] | None = None


@dataclass(frozen=True)
class ParsedStatement:
    transactions: list[ParsedTransaction]
    bank_slug: str = "unknown"
    account_name: str = ""
    source_hint: str = ""
    statement_kind: str = "bank"
    instrument_confidence: str = "unknown"
    parser_name: str = "generic_table"
    parser_version: str = "1"
    period_start: date | None = None
    period_end: date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    statement_balance: Decimal | None = None
    total_amount_due: Decimal | None = None
    minimum_amount_due: Decimal | None = None
    available_credit: Decimal | None = None
