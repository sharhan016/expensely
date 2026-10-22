"""Basic period report: what moved between two dates, split so nothing is called spending by guess.

One place decides which movements are neutral (they move money between her own places, or into
cash) so every view and export agrees. Every total is the sum of rows that can be listed.
"""

import calendar
import csv
import io
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.cash import neutral_movement_ids
from app.coverage import date_label
from app.coverage_service import account_entry
from app.models import (
    Account,
    Purpose,
    Transaction,
    TransactionLink,
    TransactionMeaning,
)
from app.money_in_motion import allocated_ids

MAX_SPAN_DAYS = 366
NEUTRAL_LINKS = ("card_payment", "refund", "reversal", "own_transfer")
OBLIGATION_PURPOSES = {
    Purpose.LOAN_GIVEN,
    Purpose.LOAN_REPAYMENT,
    Purpose.ENTRUSTED,
    Purpose.REIMBURSEMENT,
}
BUCKETS = (
    "spending",
    "income",
    "refunds",
    "own_places",
    "cash_withdrawal",
    "in_motion",
    "returns",
    "obligations",
    "unexplained",
)
BUCKET_LABELS = {
    "spending": "Marked as spending",
    "income": "Marked as income",
    "refunds": "Refunds",
    "own_places": "Moving between your own places",
    "cash_withdrawal": "Withdrawn as cash",
    "in_motion": "Sent out, expected back",
    "returns": "Returned to you",
    "obligations": "Loans, entrusted money and reimbursements",
    "unexplained": "Not yet explained",
}
_KIND_TEXT = {
    "gap": "no statement covers",
    "bridged": "balances connect but there are no statement rows for",
    "discontinuity": "the balances do not connect across",
    "difference": "a statement does not add up for",
    "unverifiable": "a statement cannot be checked for",
    "unverified": "the check is out of date for",
    "invalidated": "something changed after the check for",
    "period_mismatch": "recorded movements do not add up with the statement for",
    "acknowledged": "you said these dates are not needed:",
}


def money(value: Decimal) -> str:
    return f"{value:.2f}"


def classify(
    transaction: Transaction,
    purpose: Purpose,
    linked: set[int],
    cash: set[int],
    in_motion: set[int] = frozenset(),
    returned: set[int] = frozenset(),
) -> str:
    if transaction.id in cash:
        return "cash_withdrawal"
    if transaction.id in linked or purpose == Purpose.TRANSFER:
        return "own_places"
    if transaction.id in in_motion:
        return "in_motion"  # only because she said it was sent out and expected back
    if transaction.id in returned:
        return "returns"  # a confirmed return is never income
    if purpose in OBLIGATION_PURPOSES:
        return "obligations"
    if purpose == Purpose.INCOME and transaction.amount > 0:
        return "income"
    if purpose == Purpose.REFUND and transaction.amount > 0:
        return "refunds"
    if purpose in (Purpose.PERSONAL, Purpose.BUSINESS) and transaction.amount < 0:
        return "spending"
    return "unexplained"


def period_rows(db: Session, user_id: int, start: date, end: date) -> list[dict]:
    transactions = db.scalars(
        select(Transaction)
        .options(selectinload(Transaction.meaning).selectinload(TransactionMeaning.counterparty))
        .where(Transaction.user_id == user_id, Transaction.txn_date.between(start, end))
        .order_by(Transaction.txn_date, Transaction.id)
    ).all()
    linked: set[int] = set()
    for link in db.scalars(
        select(TransactionLink).where(
            TransactionLink.user_id == user_id,
            TransactionLink.status == "confirmed",
            TransactionLink.link_type.in_(NEUTRAL_LINKS),
        )
    ):
        linked.update((link.outflow_transaction_id, link.inflow_transaction_id))
    cash = neutral_movement_ids(db, user_id)
    in_motion = allocated_ids(db, user_id, "out")
    returned = allocated_ids(db, user_id, "return")
    names = dict(
        db.execute(select(Account.id, Account.name).where(Account.user_id == user_id)).all()
    )
    rows = []
    for transaction in transactions:
        meaning = transaction.meaning
        purpose = meaning.purpose if meaning else transaction.purpose
        counterparty = meaning.counterparty if meaning else None
        rows.append(
            {
                "id": transaction.id,
                "txn_date": transaction.txn_date,
                "account_id": transaction.account_id,
                "account_name": names.get(transaction.account_id, ""),
                "description": transaction.description,
                "amount": transaction.amount,
                "bucket": classify(transaction, purpose, linked, cash, in_motion, returned),
                "counterparty_id": counterparty.id if counterparty else None,
                "counterparty_name": counterparty.display_name if counterparty else "",
                "counterparty_confirmed": bool(counterparty and counterparty.is_confirmed),
            }
        )
    return rows


def row_json(row: dict) -> dict:
    return {
        "id": row["id"],
        "txn_date": row["txn_date"].isoformat(),
        "account_id": row["account_id"],
        "account_name": row["account_name"],
        "description": row["description"],
        "amount": money(row["amount"]),
        "bucket": row["bucket"],
        "counterparty_name": row["counterparty_name"],
    }


def _coverage(db: Session, user_id: int, start: date, end: date) -> dict:
    accounts = db.scalars(
        select(Account)
        .where(Account.user_id == user_id, Account.in_scope.is_(True))
        .order_by(Account.name)
    ).all()
    if not accounts:
        return {
            "status": "none",
            "notes": [
                "No account is being checked, so I cannot say how much of this period is covered."
            ],
        }
    notes: list[str] = []
    partial = False
    for account in accounts:
        coverage = account_entry(db, account).coverage
        cursor = start
        for covered_start, covered_end in coverage.covered:
            if covered_end < cursor or covered_start > end:
                continue
            if covered_start > cursor:
                partial = True
                notes.append(
                    f"{account.name}: not checked "
                    f"{date_label(cursor)} to {date_label(covered_start - timedelta(days=1))}."
                )
            cursor = max(cursor, covered_end + timedelta(days=1))
        if cursor <= end:
            partial = True
            notes.append(f"{account.name}: not checked {date_label(cursor)} to {date_label(end)}.")
        for segment in coverage.segments:
            if segment.kind == "covered" or segment.end < start or segment.start > end:
                continue
            clip_start, clip_end = max(segment.start, start), min(segment.end, end)
            span = (
                date_label(clip_start)
                if clip_start == clip_end
                else f"{date_label(clip_start)} to {date_label(clip_end)}"
            )
            notes.append(f"{account.name}: {_KIND_TEXT.get(segment.kind, segment.kind)} {span}.")
    return {"status": "partial" if partial else "covered", "notes": notes}


def build_report(db: Session, user_id: int, start: date, end: date) -> dict:
    rows = period_rows(db, user_id, start, end)
    buckets = {name: {"count": 0, "out": Decimal(0), "in": Decimal(0)} for name in BUCKETS}
    days: dict[date, dict] = {}
    counterparties: dict[tuple, dict] = {}
    incoming = 0
    for row in rows:
        amount = row["amount"]
        slot = buckets[row["bucket"]]
        slot["count"] += 1
        slot["out" if amount < 0 else "in"] += abs(amount)
        day = days.setdefault(row["txn_date"], {"count": 0, "out": Decimal(0), "in": Decimal(0)})
        day["count"] += 1
        day["out" if amount < 0 else "in"] += abs(amount)
        counts_as_activity = row["bucket"] not in ("own_places", "cash_withdrawal")
        if counts_as_activity and amount > 0:
            incoming += 1
        if counts_as_activity and amount < 0 and row["counterparty_id"]:
            entry = counterparties.setdefault(
                (row["counterparty_id"],),
                {
                    "name": row["counterparty_name"],
                    "confirmed": row["counterparty_confirmed"],
                    "count": 0,
                    "out": Decimal(0),
                },
            )
            entry["count"] += 1
            entry["out"] += abs(amount)
    people = sorted(counterparties.values(), key=lambda item: (-item["count"], item["name"]))
    coverage = _coverage(db, user_id, start, end)
    cash = buckets["cash_withdrawal"]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "movement_count": len(rows),
        "empty": not rows,
        "buckets": {
            name: {"count": v["count"], "out": money(v["out"]), "in": money(v["in"])}
            for name, v in buckets.items()
        },
        "labels": BUCKET_LABELS,
        "days": [
            {
                "date": d.isoformat(),
                "count": v["count"],
                "out": money(v["out"]),
                "in": money(v["in"]),
            }
            for d, v in sorted(days.items())
        ],
        "counterparties": [
            {
                "name": p["name"],
                "confirmed": p["confirmed"],
                "count": p["count"],
                "out": money(p["out"]),
            }
            for p in people
        ],
        "coverage": coverage,
        "cash_note": (
            f"Withdrawn as cash: ₹{money(cash['out'])}. I cannot see how it was spent."
            if cash["count"]
            else ""
        ),
        "summary": summarize(start, end, len(rows), people, incoming, coverage, buckets),
    }


def summarize(start, end, count, people, incoming, coverage, buckets) -> str:
    span = f"{date_label(start)} to {date_label(end)}"
    if not count:
        text = (
            f"I have no movements recorded for {span}. "
            "Send me the statements that cover these dates."
        )
    else:
        confirmed = [p for p in people if p["confirmed"]]
        others = [p for p in people if not p["confirmed"]]
        parts = [
            f"{p['count']} payment{'s' if p['count'] != 1 else ''} to {p['name']}"
            for p in confirmed[:3]
        ]
        rest = sum(p["count"] for p in confirmed[3:])
        if rest:
            parts.append(f"{rest} to other confirmed names")
        if incoming:
            parts.append(f"{incoming} incoming")
        text = f"{span}: {count} movement{'s' if count != 1 else ''}"
        text += f", including {', '.join(parts)}." if parts else "."
        if others:
            total = sum(p["count"] for p in others)
            text += (
                f" {total} payment{'s' if total != 1 else ''} went to names I detected but you "
                "have not confirmed."
            )
        unexplained = buckets["unexplained"]
        if unexplained["count"]:
            text += (
                f" Not yet explained: {unexplained['count']} movement"
                f"{'s' if unexplained['count'] != 1 else ''} "
                f"(₹{money(unexplained['out'])} out, ₹{money(unexplained['in'])} in)."
            )
        motion = buckets["in_motion"]
        if motion["count"]:
            text += (
                f" Sent out and expected back: ₹{money(motion['out'])} (not counted as spending)."
            )
        returns = buckets["returns"]
        if returns["count"]:
            text += f" Returned to you: ₹{money(returns['in'])} (not counted as income)."
        spending = buckets["spending"]
        if spending["count"]:
            text += f" Marked as spending: ₹{money(spending['out'])}."
    if coverage["status"] == "partial":
        text += " Only part of this period is covered by checked statements."
    elif coverage["status"] == "none":
        text += " I cannot say how much of this period is covered."
    return text


# ---- exports ---------------------------------------------------------------------------------

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _safe_text(value: str) -> str:
    """Stop a spreadsheet from running a narration as a formula (text columns only)."""
    value = " ".join(str(value).split())
    return f"'{value}" if value.startswith(_FORMULA_START) else value


def export_table(rows: list[dict], delimiter: str) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
    writer.writerow(["Date", "Account", "Description", "Amount", "Bucket", "Counterparty"])
    for row in rows:
        writer.writerow(
            [
                row["txn_date"].isoformat(),
                _safe_text(row["account_name"]),
                _safe_text(row["description"]),
                money(row["amount"]),  # numbers stay plain so they sum in Excel
                BUCKET_LABELS[row["bucket"]],
                _safe_text(row["counterparty_name"]),
            ]
        )
    return buffer.getvalue()


# ---- date ranges -----------------------------------------------------------------------------

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
_MONTHS["sept"] = 9
_TO = r"(?:to|and|until|till|through|-|\u2013|\u2014)"
_TEXT_RANGE = re.compile(
    rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+([a-z]{{3,9}})(?:\s+(\d{{4}}))?\s*{_TO}\s*"
    r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})(?:\s+(\d{4}))?",
    re.I,
)
_NUMERIC_RANGE = re.compile(
    rf"(\d{{1,2}})/(\d{{1,2}})/(\d{{4}})\s*{_TO}\s*(\d{{1,2}})/(\d{{1,2}})/(\d{{4}})"
)


@dataclass(frozen=True)
class ParsedRange:
    start: date
    end: date
    assumed: bool  # true when part of the range was inferred and must be confirmed


def _make(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_range(text: str, *, today: date) -> ParsedRange | None:
    lowered = text.lower()
    if "last month" in lowered:
        first = today.replace(day=1)
        end = first - timedelta(days=1)
        return ParsedRange(end.replace(day=1), end, True)
    numeric = _NUMERIC_RANGE.search(lowered)
    if numeric:
        d1, m1, y1, d2, m2, y2 = (int(part) for part in numeric.groups())
        start, end = _make(y1, m1, d1), _make(y2, m2, d2)
        return ParsedRange(start, end, False) if start and end and start <= end else None
    match = _TEXT_RANGE.search(lowered)
    if not match:
        return None
    d1, name1, y1, d2, name2, y2 = match.groups()
    if name1 not in _MONTHS or name2 not in _MONTHS:
        return None
    m1, m2, d1, d2 = _MONTHS[name1], _MONTHS[name2], int(d1), int(d2)
    assumed = y1 is None and y2 is None
    end_year = int(y2) if y2 else int(y1) if y1 else today.year
    start_year = int(y1) if y1 else end_year
    end = _make(end_year, m2, d2)
    start = _make(start_year, m1, d1)
    if start and end and start > end and not y1 and not y2 and m1 > m2:
        start = _make(start_year - 1, m1, d1)  # a range that wraps over New Year
    if not start or not end or start > end:
        return None
    return ParsedRange(start, end, assumed or not (y1 and y2))
