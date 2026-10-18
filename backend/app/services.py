import hashlib
import re
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Person, Purpose, Transaction

_SPACE = re.compile(r"\s+")
_LONG_IDENTIFIER = re.compile(r"\b\d{6,}\b")
_PUNCTUATION = re.compile(r"[^A-Z0-9 ]+")


def normalized_description(value: str) -> str:
    text = _LONG_IDENTIFIER.sub(" ", value.upper())
    text = _PUNCTUATION.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def suggest_from_history(
    description: str, history: list[tuple[str, Purpose]]
) -> tuple[Purpose | None, str]:
    """Return a deterministic description-based suggestion; amounts are intentionally absent."""
    candidate = normalized_description(description)
    if not candidate:
        return None, "unresolved"
    best_purpose = None
    best_score = 0.0
    for previous_description, purpose in history:
        previous = normalized_description(previous_description)
        if not previous:
            continue
        if previous == candidate:
            return purpose, "confident"
        score = SequenceMatcher(None, candidate, previous).ratio()
        if score > best_score:
            best_score, best_purpose = score, purpose
    if best_score >= 0.82:
        return best_purpose, "suggested"
    return None, "unresolved"


def dedupe_hash(
    account_id: int, txn_date: date, amount: Decimal, description: str, reference: str = ""
) -> str:
    normalized = _SPACE.sub(" ", description).strip().upper()
    identity = (
        f"{account_id}|{txn_date.isoformat()}|{amount:.2f}|{normalized}|{reference.strip().upper()}"
    )
    return hashlib.sha256(identity.encode()).hexdigest()


def person_balances(db: Session, person: Person) -> tuple[Decimal, Decimal]:
    entrusted, loans_given, loans_repaid = db.execute(
        select(
            func.coalesce(
                func.sum(
                    case((Transaction.purpose == Purpose.ENTRUSTED, Transaction.amount), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((Transaction.purpose == Purpose.LOAN_GIVEN, -Transaction.amount), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (Transaction.purpose == Purpose.LOAN_REPAYMENT, Transaction.amount), else_=0
                    )
                ),
                0,
            ),
        ).where(Transaction.user_id == person.user_id, Transaction.person_id == person.id)
    ).one()
    return Decimal(entrusted), Decimal(loans_given) - Decimal(loans_repaid)


def calculate_reconciliation(
    opening_balance: Decimal,
    movements: list[Decimal],
    statement_closing_balance: Decimal,
) -> tuple[Decimal, Decimal]:
    expected = opening_balance + sum(movements, Decimal("0"))
    return expected, statement_closing_balance - expected
