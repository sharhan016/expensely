import hashlib
import re
from datetime import date
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Person, Purpose, Transaction

_SPACE = re.compile(r"\s+")


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
