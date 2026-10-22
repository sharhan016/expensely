"""Investigation tools: plain functions with validated inputs that return evidence the user owns.

No model is involved. Results carry ids and amounts but never narrations or names, and every
function is scoped to one user. A tool states what the records contain; it never infers a cause.
"""

import itertools
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.cash import neutral_movement_ids
from app.coverage_service import account_entry, account_spans
from app.models import (
    Account,
    CashWithdrawalMarker,
    Purpose,
    Transaction,
    TransactionLink,
    TransactionMeaning,
)

logger = logging.getLogger("expense.investigate")
MAX_WINDOW_DAYS = 366
CLOSE_TOLERANCE = Decimal("0.02")
MAX_CASH_CANDIDATES = 60

Amount = Annotated[
    Decimal, Field(gt=0, lt=Decimal("1000000000000"), max_digits=14, decimal_places=2)
]


class ToolError(Exception):
    """A tool could not answer. The code is safe to show; the message never echoes input."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _check_window(start: date | None, end: date | None) -> None:
    if (start is None) != (end is None):
        raise ValueError("give both dates or neither")
    if start and end:
        if end < start:
            raise ValueError("the last date is before the first")
        if (end - start).days >= MAX_WINDOW_DAYS:
            raise ValueError("the window is longer than a year")


class OptionalWindow(BaseModel):
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def _window(self):
        _check_window(self.date_from, self.date_to)
        return self


class RequiredWindow(BaseModel):
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def _window(self):
        _check_window(self.date_from, self.date_to)
        return self


class FindByAmountIn(OptionalWindow):
    amount: Amount
    include_close: bool = False
    limit: int = Field(default=50, ge=1, le=200)


class FindCashIn(OptionalWindow):
    amount: Amount
    limit: int = Field(default=10, ge=1, le=50)


class RelatedLinksIn(BaseModel):
    transaction_id: int = Field(ge=1, le=2**31 - 1)


class OpenExpectationsIn(BaseModel):
    transaction_id: int | None = Field(default=None, ge=1, le=2**31 - 1)


class UnresolvedIn(RequiredWindow):
    limit: int = Field(default=50, ge=1, le=200)


class CoverageGapsIn(RequiredWindow):
    pass


class DiscontinuitiesIn(RequiredWindow):
    amount: Amount | None = None


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _in_window(query, window):
    start, end = window
    if start and end:
        query = query.where(Transaction.txn_date.between(start, end))
    return query


def _row(transaction: Transaction, account_names: dict[int, str], cash: set[int]) -> dict:
    return {
        "transaction_id": transaction.id,
        "txn_date": transaction.txn_date.isoformat(),
        "account_id": transaction.account_id,
        "account_name": account_names.get(transaction.account_id, ""),
        "amount": _money(transaction.amount),
        "direction": "out" if transaction.amount < 0 else "in",
        "cash_withdrawal": transaction.id in cash,
        "evidence": f"txn:{transaction.id}",
    }


def _names(db: Session, user_id: int) -> dict[int, str]:
    return dict(
        db.execute(select(Account.id, Account.name).where(Account.user_id == user_id)).all()
    )


def find_by_amount(db: Session, user_id: int, data: FindByAmountIn) -> dict:
    amount = data.amount
    exact = or_(Transaction.amount == amount, Transaction.amount == -amount)
    conditions = [exact]
    if data.include_close:
        low, high = amount * (1 - CLOSE_TOLERANCE), amount * (1 + CLOSE_TOLERANCE)
        conditions.append(Transaction.amount.between(low, high))
        conditions.append(Transaction.amount.between(-high, -low))
    query = _in_window(
        select(Transaction).where(Transaction.user_id == user_id, or_(*conditions)),
        (data.date_from, data.date_to),
    ).order_by(Transaction.txn_date, Transaction.id)
    cash, names = neutral_movement_ids(db, user_id), _names(db, user_id)
    matches = []
    for transaction in db.scalars(query):
        item = _row(transaction, names, cash)
        item["kind"] = "exact" if abs(transaction.amount) == amount else "close"
        matches.append(item)
    return {"matches": matches[: data.limit], "total": len(matches)}


def find_cash_withdrawals(db: Session, user_id: int, data: FindCashIn) -> dict:
    query = _in_window(
        select(Transaction)
        .join(CashWithdrawalMarker, CashWithdrawalMarker.transaction_id == Transaction.id)
        .where(
            Transaction.user_id == user_id,
            CashWithdrawalMarker.user_id == user_id,
            CashWithdrawalMarker.is_cash_withdrawal.is_(True),
        ),
        (data.date_from, data.date_to),
    ).order_by(Transaction.txn_date, Transaction.id)
    withdrawals = list(db.scalars(query))
    truncated = len(withdrawals) > MAX_CASH_CANDIDATES
    withdrawals = withdrawals[-MAX_CASH_CANDIDATES:]
    combinations = []
    for size in (1, 2, 3):
        for combo in itertools.combinations(withdrawals, size):
            if sum((-item.amount for item in combo), Decimal(0)) == data.amount:
                combinations.append(
                    {
                        "transaction_ids": [item.id for item in combo],
                        "dates": [item.txn_date.isoformat() for item in combo],
                        "amounts": [_money(-item.amount) for item in combo],
                        "total": _money(data.amount),
                        "label": "possible",  # never the cause: only arithmetic on the records
                        "evidence": [f"txn:{item.id}" for item in combo],
                    }
                )
    return {
        "combinations": combinations[: data.limit],
        "withdrawals_considered": len(withdrawals),
        "truncated": truncated,
    }


def related_links(db: Session, user_id: int, data: RelatedLinksIn) -> dict:
    transaction = db.scalar(
        select(Transaction).where(
            Transaction.id == data.transaction_id, Transaction.user_id == user_id
        )
    )
    if transaction is None:
        raise ToolError("not_found")
    links = db.scalars(
        select(TransactionLink).where(
            TransactionLink.user_id == user_id,
            or_(
                TransactionLink.outflow_transaction_id == transaction.id,
                TransactionLink.inflow_transaction_id == transaction.id,
            ),
        )
    ).all()
    out = []
    for link in links:
        is_out = link.outflow_transaction_id == transaction.id
        other = link.inflow_transaction_id if is_out else link.outflow_transaction_id
        partner = db.get(Transaction, other)
        out.append(
            {
                "other_date": partner.txn_date.isoformat() if partner else None,
                "link_id": link.id,
                "link_type": link.link_type,
                "status": link.status,
                "role": "outflow" if is_out else "inflow",
                "other_transaction_id": other,
                "evidence": f"txn:{other}",
            }
        )
    return {
        "transaction_id": transaction.id,
        "links": out,
        "cash_withdrawal": transaction.id in neutral_movement_ids(db, user_id),
    }


def open_expectations(db: Session, user_id: int, data: OpenExpectationsIn) -> dict:
    """What she marked as sent out and expected back, and returns waiting for her confirmation.
    With a transaction id, only the expectations that movement belongs to (or may settle)."""
    from app.money_in_motion import outstanding, return_proposals

    ref = None
    if data.transaction_id is not None:
        if not db.scalar(
            select(Transaction.id).where(
                Transaction.id == data.transaction_id, Transaction.user_id == user_id
            )
        ):
            raise ToolError("not_found")
        ref = f"txn:{data.transaction_id}"
    proposals = [
        item for item in return_proposals(db, user_id) if ref is None or item["evidence"] == ref
    ]
    pending = {item["expectation_id"] for item in proposals}
    items = []
    for item in outstanding(db, user_id):
        if item["status"] == "not_expected":
            continue
        role = None
        if ref and ref in item["evidence"]:
            role = "out"
        elif ref and ref in item["returns"]:
            role = "return"
        elif item["expectation_id"] in pending:
            role = "proposal"
        if ref is None or role:
            items.append({**item, "role": role})
    return {"expectations": items, "return_proposals": proposals}


def unresolved_in_window(db: Session, user_id: int, data: UnresolvedIn) -> dict:
    rows = db.scalars(
        select(Transaction)
        .options(selectinload(Transaction.meaning))
        .where(
            Transaction.user_id == user_id,
            Transaction.txn_date.between(data.date_from, data.date_to),
        )
        .order_by(Transaction.txn_date, Transaction.id)
    ).all()
    cash, names = neutral_movement_ids(db, user_id), _names(db, user_id)
    unresolved = [
        item
        for item in rows
        if (item.meaning.purpose if item.meaning else item.purpose) == Purpose.OTHER
    ]
    return {
        "items": [_row(item, names, cash) for item in unresolved[: data.limit]],
        "total": len(unresolved),
    }


def coverage_gaps(db: Session, user_id: int, data: CoverageGapsIn) -> dict:
    """Dates inside the window that no checked statement covers, per account in scope."""
    gaps = []
    accounts = db.scalars(
        select(Account)
        .where(Account.user_id == user_id, Account.in_scope.is_(True))
        .order_by(Account.id)
    ).all()
    for account in accounts:
        cursor = data.date_from
        for covered_start, covered_end in account_entry(db, account).coverage.covered:
            if covered_end < cursor or covered_start > data.date_to:
                continue
            if covered_start > cursor:
                gaps.append(_gap(account.id, cursor, covered_start - timedelta(days=1)))
            cursor = max(cursor, covered_end + timedelta(days=1))
        if cursor <= data.date_to:
            gaps.append(_gap(account.id, cursor, data.date_to))
    for gap in gaps:
        gap["account_name"] = next(a.name for a in accounts if a.id == gap["account_id"])
    return {"gaps": gaps, "accounts": len(accounts)}


def _gap(account_id: int, start: date, end: date) -> dict:
    return {
        "account_id": account_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "evidence": f"cov:{account_id}:{start.isoformat()}:{end.isoformat()}",
    }


def balance_discontinuities(db: Session, user_id: int, data: DiscontinuitiesIn) -> dict:
    """Where one statement's closing balance and the next one's opening balance differ."""
    found = []
    accounts = db.scalars(select(Account).where(Account.user_id == user_id)).all()
    for account in accounts:
        spans = sorted(account_spans(db, account), key=lambda item: (item.start, item.end))
        for previous, following in itertools.pairwise(spans):
            if following.start <= previous.end:
                continue  # overlapping statements are re-uploads, not a break
            if previous.closing is None or following.opening is None:
                continue
            difference = following.opening - previous.closing
            if difference == 0:
                continue
            if following.start < data.date_from or previous.end > data.date_to:
                continue
            found.append(
                {
                    "account_id": account.id,
                    "after": previous.end.isoformat(),
                    "before": following.start.isoformat(),
                    "difference": _money(abs(difference)),
                    "matches_amount": data.amount is not None and abs(difference) == data.amount,
                    "evidence": [
                        f"disc:{account.id}:{previous.key}:{following.key}",
                        f"stmt:{previous.key}",
                        f"stmt:{following.key}",
                    ],
                }
            )
    return {"discontinuities": found}


TOOLS = {
    "find_by_amount": (FindByAmountIn, find_by_amount),
    "find_cash_withdrawals": (FindCashIn, find_cash_withdrawals),
    "related_links": (RelatedLinksIn, related_links),
    "open_expectations": (OpenExpectationsIn, open_expectations),
    "unresolved_in_window": (UnresolvedIn, unresolved_in_window),
    "coverage_gaps": (CoverageGapsIn, coverage_gaps),
    "balance_discontinuities": (DiscontinuitiesIn, balance_discontinuities),
}
_ = TransactionMeaning  # eager relationship target for selectinload
