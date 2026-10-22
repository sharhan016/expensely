"""Money in motion: what she sent out and expects back, and the returns that settle it.

Default is "unknown until she says": a payment to a person is never assumed to be an expense or a
loan. Nothing here changes an obligation without her explicit answer, every step is reversible,
and no allocation can exceed the movement it comes from.
"""

import itertools
import re
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.cash import neutral_movement_ids
from app.meanings import apply_meaning_change
from app.models import (
    Account,
    Counterparty,
    ExpectationAllocation,
    InstrumentType,
    MoneyExpectation,
    Purpose,
    Transaction,
    TransactionLink,
    TransactionMeaning,
)

NEUTRAL_LINK_TYPES = ("card_payment", "refund", "reversal", "own_transfer")
TRANSFER_WINDOW_DAYS = 3


class AllocationError(Exception):
    """A request that would over-allocate a movement or an expectation."""


def money(value: Decimal) -> str:
    return f"{value:.2f}"


def _linked_ids(db: Session, user_id: int) -> set[int]:
    ids: set[int] = set()
    for link in db.scalars(
        select(TransactionLink).where(
            TransactionLink.user_id == user_id,
            TransactionLink.status == "confirmed",
            TransactionLink.link_type.in_(NEUTRAL_LINK_TYPES),
        )
    ):
        ids.update((link.outflow_transaction_id, link.inflow_transaction_id))
    return ids


def allocated_ids(db: Session, user_id: int, role: str) -> set[int]:
    return set(
        db.scalars(
            select(ExpectationAllocation.transaction_id).where(
                ExpectationAllocation.user_id == user_id,
                ExpectationAllocation.role == role,
                ExpectationAllocation.status == "confirmed",
            )
        )
    )


def _used(db: Session, transaction_id: int) -> Decimal:
    return db.scalar(
        select(func.coalesce(func.sum(ExpectationAllocation.amount), 0)).where(
            ExpectationAllocation.transaction_id == transaction_id,
            ExpectationAllocation.status == "confirmed",
        )
    ) or Decimal(0)


def _meaning_purpose(transaction: Transaction) -> Purpose:
    return transaction.meaning.purpose if transaction.meaning else transaction.purpose


def _bank_transactions(db: Session, user_id: int):
    return (
        select(Transaction)
        .join(Account, Account.id == Transaction.account_id)
        .options(selectinload(Transaction.meaning).selectinload(TransactionMeaning.counterparty))
        .where(
            Transaction.user_id == user_id,
            Account.user_id == user_id,
            Account.instrument_type != InstrumentType.CREDIT_CARD,
        )
    )


# ---- questions (one per person, never per row) -----------------------------------------------


def eligible_outgoing(db: Session, user_id: int, counterparty_id: int | None = None):
    skip = _linked_ids(db, user_id) | neutral_movement_ids(db, user_id)
    skip |= allocated_ids(db, user_id, "out")
    rows = []
    for item in db.scalars(
        _bank_transactions(db, user_id)
        .where(Transaction.amount < 0)
        .order_by(Transaction.txn_date, Transaction.id)
    ):
        meaning = item.meaning
        if (
            item.id in skip
            or meaning is None
            or meaning.counterparty_id is None
            or meaning.purpose != Purpose.OTHER
            or (counterparty_id is not None and meaning.counterparty_id != counterparty_id)
        ):
            continue
        rows.append(item)
    return rows


def pending_questions(db: Session, user_id: int) -> list[dict]:
    groups: dict[int, list[Transaction]] = {}
    for item in eligible_outgoing(db, user_id):
        groups.setdefault(item.meaning.counterparty_id, []).append(item)
    questions = []
    for counterparty_id, rows in groups.items():
        questions.append(
            {
                "counterparty_id": counterparty_id,
                "counterparty_name": rows[0].meaning.counterparty.display_name,
                "count": len(rows),
                "total": money(sum((-r.amount for r in rows), Decimal(0))),
                "first_date": rows[0].txn_date.isoformat(),
                "last_date": rows[-1].txn_date.isoformat(),
                "transaction_ids": [r.id for r in rows],
                "evidence": [f"txn:{r.id}" for r in rows],
            }
        )
    return sorted(questions, key=lambda q: (-Decimal(q["total"]), q["counterparty_name"]))


def allocate(
    db: Session,
    user_id: int,
    expectation: MoneyExpectation,
    transaction: Transaction,
    role: str,
    amount: Decimal,
    source: str = "user_confirmed",
) -> ExpectationAllocation:
    """Assign part of a movement. The movement row is locked so two requests cannot both fit."""
    db.execute(
        select(Transaction.id).where(Transaction.id == transaction.id).with_for_update()
    ).all()
    if amount <= 0 or _used(db, transaction.id) + amount > abs(transaction.amount):
        raise AllocationError("This movement does not have that much left to assign.")
    if role == "return":
        sent, returned = _totals(db, expectation.id)
        if returned + amount > sent:
            raise AllocationError("That is more than is still expected back.")
    allocation = ExpectationAllocation(
        user_id=user_id,
        expectation_id=expectation.id,
        transaction_id=transaction.id,
        role=role,
        amount=amount,
        status="confirmed",
        decision_source=source,
    )
    db.add(allocation)
    db.flush()
    return allocation


def _totals(db: Session, expectation_id: int) -> tuple[Decimal, Decimal]:
    out = {}
    for role in ("out", "return"):
        out[role] = db.scalar(
            select(func.coalesce(func.sum(ExpectationAllocation.amount), 0)).where(
                ExpectationAllocation.expectation_id == expectation_id,
                ExpectationAllocation.role == role,
                ExpectationAllocation.status == "confirmed",
            )
        ) or Decimal(0)
    return Decimal(out["out"]), Decimal(out["return"])


def answer_question(
    db: Session,
    user: int,
    counterparty: Counterparty,
    choice: str,
    transaction_ids: list[int] | None,
) -> int:
    """Apply her answer to the still-unanswered payments. Replays find nothing left to do."""
    rows = eligible_outgoing(db, user, counterparty.id)
    if transaction_ids is not None:
        rows = [row for row in rows if row.id in set(transaction_ids)]
    if not rows:
        return 0
    if choice == "expense":
        for row in rows:
            meaning = row.meaning
            apply_meaning_change(
                db,
                transaction=row,
                actor_user_id=user,
                purpose=Purpose.PERSONAL,
                person_id=meaning.person_id,
                employer_id=meaning.employer_id,
                trip_id=meaning.trip_id,
                counterparty_id=meaning.counterparty_id,
                notes=meaning.notes,
                reason="You said this payment was an expense",
            )
        return len(rows)
    expectation = db.scalar(
        select(MoneyExpectation)
        .where(
            MoneyExpectation.user_id == user,
            MoneyExpectation.counterparty_id == counterparty.id,
            MoneyExpectation.status == "tracking",
        )
        .order_by(MoneyExpectation.id.desc())
        .limit(1)
    )
    if expectation is None:
        expectation = MoneyExpectation(
            user_id=user,
            kind="person",
            counterparty_id=counterparty.id,
            status="tracking",
            opened_on=min(row.txn_date for row in rows),
        )
        db.add(expectation)
        db.flush()
    for row in rows:
        allocate(db, user, expectation, row, "out", -row.amount)
    expectation.opened_on = min(expectation.opened_on, min(row.txn_date for row in rows))
    return len(rows)


# ---- outstanding and returns -----------------------------------------------------------------


def _state(expectation: MoneyExpectation, sent: Decimal, returned: Decimal) -> str:
    if expectation.status != "tracking":
        return expectation.status
    if returned == 0:
        return "open"
    return "settled" if returned >= sent else "partly_settled"


def outstanding(db: Session, user_id: int, *, today: date | None = None) -> list[dict]:
    today = today or date.today()
    result = []
    names = dict(
        db.execute(
            select(Counterparty.id, Counterparty.display_name).where(
                Counterparty.user_id == user_id
            )
        ).all()
    )
    for expectation in db.scalars(
        select(MoneyExpectation)
        .where(MoneyExpectation.user_id == user_id)
        .order_by(MoneyExpectation.opened_on, MoneyExpectation.id)
    ):
        sent, returned = _totals(db, expectation.id)
        if sent == 0 and returned == 0:
            continue
        allocations = db.execute(
            select(ExpectationAllocation.role, ExpectationAllocation.transaction_id)
            .join(Transaction, Transaction.id == ExpectationAllocation.transaction_id)
            .where(
                ExpectationAllocation.expectation_id == expectation.id,
                ExpectationAllocation.status == "confirmed",
            )
            .order_by(Transaction.txn_date, Transaction.id)
        ).all()
        result.append(
            {
                "expectation_id": expectation.id,
                "counterparty_id": expectation.counterparty_id,
                "counterparty_name": names.get(expectation.counterparty_id, ""),
                "sent": money(sent),
                "returned": money(returned),
                "remaining": money(max(sent - returned, Decimal(0))),
                "status": _state(expectation, sent, returned),
                "since": expectation.opened_on.isoformat(),
                "age_days": (today - expectation.opened_on).days,
                "evidence": [f"txn:{t}" for r, t in allocations if r == "out"],
                "returns": [f"txn:{t}" for r, t in allocations if r == "return"],
            }
        )
    return result


def return_proposals(db: Session, user_id: int) -> list[dict]:
    proposals = []
    skip = _linked_ids(db, user_id) | neutral_movement_ids(db, user_id)
    incoming = list(
        db.scalars(
            _bank_transactions(db, user_id)
            .where(Transaction.amount > 0)
            .order_by(Transaction.txn_date, Transaction.id)
        )
    )
    dismissed = {
        (a.expectation_id, a.transaction_id)
        for a in db.scalars(
            select(ExpectationAllocation).where(
                ExpectationAllocation.user_id == user_id,
                ExpectationAllocation.status == "dismissed",
            )
        )
    }
    for expectation in db.scalars(
        select(MoneyExpectation).where(
            MoneyExpectation.user_id == user_id, MoneyExpectation.status == "tracking"
        )
    ):
        sent, returned = _totals(db, expectation.id)
        remaining = sent - returned
        if remaining <= 0:
            continue
        for item in incoming:
            meaning = item.meaning
            if (
                item.id in skip
                or meaning is None
                or meaning.counterparty_id != expectation.counterparty_id
                or item.txn_date < expectation.opened_on
                or (expectation.id, item.id) in dismissed
            ):
                continue
            available = item.amount - _used(db, item.id)
            if available <= 0:
                continue
            proposals.append(
                {
                    "expectation_id": expectation.id,
                    "transaction_id": item.id,
                    "counterparty_name": meaning.counterparty.display_name,
                    "date": item.txn_date.isoformat(),
                    "amount": money(available),
                    "remaining": money(remaining),
                    "evidence": f"txn:{item.id}",
                }
            )
    return proposals


def confirm_return(
    db: Session,
    user_id: int,
    expectation: MoneyExpectation,
    transaction: Transaction,
    amount: Decimal | None,
) -> ExpectationAllocation:
    if transaction.amount <= 0:
        raise AllocationError("Only money that came in can be a return.")
    if expectation.status != "tracking":
        raise AllocationError("This is no longer being tracked as expected back.")
    if amount is None:
        sent, returned = _totals(db, expectation.id)
        amount = min(transaction.amount - _used(db, transaction.id), sent - returned)
    return allocate(db, user_id, expectation, transaction, "return", amount)


def dismiss_return(
    db: Session, user_id: int, expectation: MoneyExpectation, transaction: Transaction
):
    exists = db.scalar(
        select(ExpectationAllocation.id).where(
            ExpectationAllocation.expectation_id == expectation.id,
            ExpectationAllocation.transaction_id == transaction.id,
            ExpectationAllocation.status == "dismissed",
        )
    )
    if not exists:
        db.add(
            ExpectationAllocation(
                user_id=user_id,
                expectation_id=expectation.id,
                transaction_id=transaction.id,
                role="return",
                amount=abs(transaction.amount),
                status="dismissed",
                decision_source="user_confirmed",
            )
        )
        db.flush()


# ---- own-account transfers -------------------------------------------------------------------

_LONG_NUMBER = re.compile(r"\d{6,}")


def _strong_evidence(out: Transaction, inn: Transaction, accounts: dict[int, Account]) -> bool:
    if out.reference and out.reference.strip().upper() == inn.reference.strip().upper():
        return True
    if set(_LONG_NUMBER.findall(out.description)) & set(_LONG_NUMBER.findall(inn.description)):
        return True
    for source, target in ((out, inn), (inn, out)):
        hint = accounts[source.account_id].account_hint[-4:]
        if len(hint) == 4 and hint.isdigit() and hint in target.description:
            return True
    return False


def own_transfer_candidates(db: Session, user_id: int) -> list[dict]:
    skip = _linked_ids(db, user_id) | neutral_movement_ids(db, user_id)
    rejected = {
        (link.outflow_transaction_id, link.inflow_transaction_id)
        for link in db.scalars(
            select(TransactionLink).where(
                TransactionLink.user_id == user_id,
                TransactionLink.link_type == "own_transfer",
                TransactionLink.status == "rejected",
            )
        )
    }
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.user_id == user_id))}
    rows = [r for r in db.scalars(_bank_transactions(db, user_id)) if r.id not in skip]
    outs = [r for r in rows if r.amount < 0]
    ins = [r for r in rows if r.amount > 0]

    def near(out: Transaction, inn: Transaction) -> bool:
        return (
            out.account_id != inn.account_id
            and abs(out.amount) == inn.amount
            and abs((out.txn_date - inn.txn_date).days) <= TRANSFER_WINDOW_DAYS
            and (out.id, inn.id) not in rejected
        )

    pairs = []
    for out, inn in itertools.product(outs, ins):
        if near(out, inn):
            pairs.append((out, inn))
    result = []
    for out, inn in pairs:
        if sum(1 for o, _ in pairs if o.id == out.id) != 1:
            continue
        if sum(1 for _, i in pairs if i.id == inn.id) != 1:
            continue
        result.append(
            {
                "outflow_id": out.id,
                "inflow_id": inn.id,
                "amount": money(inn.amount),
                "out_date": out.txn_date.isoformat(),
                "in_date": inn.txn_date.isoformat(),
                "strong": _strong_evidence(out, inn, accounts),
                "evidence": [f"txn:{out.id}", f"txn:{inn.id}"],
            }
        )
    return result


def sync_own_transfers(db: Session, user_id: int) -> int:
    """Link only unique pairs with real evidence beyond amount and date. Idempotent."""
    created = 0
    for pair in own_transfer_candidates(db, user_id):
        if not pair["strong"]:
            continue
        db.add(
            TransactionLink(
                user_id=user_id,
                link_type="own_transfer",
                outflow_transaction_id=pair["outflow_id"],
                inflow_transaction_id=pair["inflow_id"],
                status="confirmed",
                confidence="high",
                decision_source="automatic_rule",
                evidence={"amount": pair["amount"], "basis": "unique pair with shared evidence"},
            )
        )
        created += 1
    if created:
        db.flush()
    return created


def summary_sentences(rows: list[dict]) -> tuple[str, list[str]]:
    from app.cash import _rupees
    from app.coverage import date_label

    if not rows:
        return (
            "Nothing is marked as sent out and expected back. When you tell me a payment "
            "should come back, I will track it here.",
            [],
        )
    parts, evidence = [], []
    for row in rows:
        text = (
            f"{row['counterparty_name']}: {_rupees(Decimal(row['remaining']))} still out of "
            f"{_rupees(Decimal(row['sent']))} sent since "
            f"{date_label(date.fromisoformat(row['since']))}"
        )
        if Decimal(row["returned"]) > 0:
            text += f", and {_rupees(Decimal(row['returned']))} has come back"
        if row["status"] in {"written_off", "not_expected"}:
            text += f" (you marked it {row['status'].replace('_', ' ')})"
        parts.append(text + ".")
        evidence += row["evidence"] + row["returns"]
    return " ".join(parts), evidence


_ = timedelta
