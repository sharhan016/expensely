"""Cash in hand as a derived bucket, not an account.

An ATM debit stays a real bank movement (it lowers the bank balance and never breaks a
reconciliation). A reversible marker says it also put cash in hand. The bucket is only ever
described as "withdrawn, not yet explained"; it is never a verified cash balance and never
counts toward reconciled-through.
"""

import re
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.coverage import date_label
from app.models import (
    Account,
    CashStatementEntry,
    CashWithdrawalMarker,
    InstrumentType,
    Transaction,
)

_SEP = r"[\s/*:.\-]*"
_WITHDRAWAL_WORDS = r"(?:WDL|WDRL|WDR|WITHDRAWAL|WITHDRAWL|WITHDRAW)"
_POSITIVE = (
    re.compile(rf"\b(?:ATM|NFS|ATW){_SEP}(?:CASH{_SEP})?{_WITHDRAWAL_WORDS}\b"),
    re.compile(rf"\bCASH{_SEP}{_WITHDRAWAL_WORDS}\b"),
    re.compile(rf"\bATM{_SEP}CASH\b(?!{_SEP}DEP)"),
)
# Fees, reversals and the like mention ATM or cash but are not cash coming out of the account.
_NEGATIVE = re.compile(
    r"\b(?:FEE|FEES|CHARGE|CHARGES|CHRG|CHG|ANNUAL|REVERSAL|REVERSED|REFUND|DECLINED|DISPUTE|"
    r"CASHBACK|CASH\s+BACK)\b"
)


def is_cash_withdrawal(description: str, amount: Decimal) -> bool:
    if amount >= 0:
        return False
    text = " ".join(description.upper().split())
    if _NEGATIVE.search(text):
        return False
    return any(pattern.search(text) for pattern in _POSITIVE)


def sync_cash_markers(db: Session, user_id: int) -> int:
    """Mark matching bank debits that have no marker yet. Never touches an existing marker,
    so a withdrawal she unmarked stays unmarked. Idempotent."""
    candidates = db.scalars(
        select(Transaction)
        .join(Account, Account.id == Transaction.account_id)
        .outerjoin(CashWithdrawalMarker, CashWithdrawalMarker.transaction_id == Transaction.id)
        .where(
            Transaction.user_id == user_id,
            Account.user_id == user_id,
            Account.instrument_type != InstrumentType.CREDIT_CARD,
            Transaction.amount < 0,
            CashWithdrawalMarker.id.is_(None),
        )
    ).all()
    created = 0
    for transaction in candidates:
        if is_cash_withdrawal(transaction.description, transaction.amount):
            db.add(
                CashWithdrawalMarker(
                    user_id=user_id,
                    transaction_id=transaction.id,
                    is_cash_withdrawal=True,
                    source="rule",
                )
            )
            created += 1
    if created:
        db.flush()
    return created


def neutral_movement_ids(db: Session, user_id: int) -> set[int]:
    """Movements that are neutral in income and spending totals because they moved money into
    the cash bucket. Totals built later must exclude these."""
    return set(
        db.scalars(
            select(CashWithdrawalMarker.transaction_id).where(
                CashWithdrawalMarker.user_id == user_id,
                CashWithdrawalMarker.is_cash_withdrawal.is_(True),
            )
        )
    )


def cash_summary(db: Session, user_id: int) -> dict:
    sync_cash_markers(db, user_id)
    rows = db.execute(
        select(CashWithdrawalMarker, Transaction)
        .join(Transaction, Transaction.id == CashWithdrawalMarker.transaction_id)
        .where(
            CashWithdrawalMarker.user_id == user_id,
            Transaction.user_id == user_id,
            CashWithdrawalMarker.is_cash_withdrawal.is_(True),
        )
        .order_by(Transaction.txn_date.desc(), Transaction.id.desc())
    ).all()
    withdrawn = sum((-transaction.amount for _, transaction in rows), Decimal("0"))
    since = min((transaction.txn_date for _, transaction in rows), default=None)
    entries = db.scalars(
        select(CashStatementEntry)
        .where(CashStatementEntry.user_id == user_id)
        .order_by(CashStatementEntry.entry_date.desc(), CashStatementEntry.id.desc())
    ).all()
    explained = sum(
        (entry.amount for entry in entries if since and entry.entry_date >= since), Decimal("0")
    )
    unexplained = max(withdrawn - explained, Decimal("0"))
    return {
        "withdrawn_total": f"{withdrawn:.2f}",
        "withdrawal_count": len(rows),
        "since": since.isoformat() if since else None,
        "explained_by_you": f"{explained:.2f}",
        "not_yet_explained": f"{unexplained:.2f}",
        "summary": cash_sentence(unexplained, since, len(rows)),
        "note": (
            "This is not a cash balance. It is what left your bank accounts as cash minus "
            "what you told me you spent; I cannot verify cash."
        ),
        "withdrawals": [
            {
                "transaction_id": transaction.id,
                "txn_date": transaction.txn_date.isoformat(),
                "description": transaction.description,
                "amount": f"{-transaction.amount:.2f}",
                "source": marker.source,
            }
            for marker, transaction in rows
        ],
        "entries": [
            {
                "id": entry.id,
                "entry_date": entry.entry_date.isoformat(),
                "amount": f"{entry.amount:.2f}",
                "note": entry.note,
                "label": "No statement behind this",
            }
            for entry in entries
        ],
    }


def _rupees(value: Decimal) -> str:
    whole, fraction = f"{value:.2f}".split(".")
    if len(whole) > 3:
        tail, head, groups = whole[-3:], whole[:-3], []
        while head:
            groups.append(head[-2:])
            head = head[:-2]
        whole = f"{','.join(reversed(groups))},{tail}"
    return f"₹{whole}.{fraction}"


def cash_sentence(unexplained: Decimal, since: date | None, count: int) -> str:
    if not count or since is None:
        return "I have not found any cash withdrawals."
    return (
        f"Withdrawn, not yet explained: {_rupees(unexplained)} since {date_label(since)} "
        f"({count} withdrawal{'s' if count != 1 else ''})."
    )
