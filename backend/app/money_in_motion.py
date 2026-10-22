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
    MotionPreference,
    Person,
    Purpose,
    Transaction,
    TransactionLink,
    TransactionMeaning,
    TransactionMeaningChange,
)
from app.services import person_balances

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


def allocated_ids(
    db: Session, user_id: int, role: str, expectation_status: tuple[str, ...] | None = None
) -> set[int]:
    """Movements with a confirmed allocation, optionally only on expectations in these states."""
    query = select(ExpectationAllocation.transaction_id).where(
        ExpectationAllocation.user_id == user_id,
        ExpectationAllocation.role == role,
        ExpectationAllocation.status == "confirmed",
    )
    if expectation_status is not None:
        query = query.join(
            MoneyExpectation, MoneyExpectation.id == ExpectationAllocation.expectation_id
        ).where(MoneyExpectation.status.in_(expectation_status))
    return set(db.scalars(query))


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
    skip |= allocated_ids(db, user_id, "out", ("tracking", "written_off"))
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
    remembered = {
        p.counterparty_id: p
        for p in db.scalars(select(MotionPreference).where(MotionPreference.user_id == user_id))
    }
    for counterparty_id, rows in groups.items():
        preference = remembered.get(counterparty_id)
        questions.append(
            {
                "remembered": (
                    preference.choice
                    if preference and any(r.id > preference.after_transaction_id for r in rows)
                    else None
                ),
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


def _totals(
    db: Session, expectation_id: int, statuses: tuple[str, ...] = ("confirmed",)
) -> tuple[Decimal, Decimal]:
    out = {}
    for role in ("out", "return"):
        out[role] = db.scalar(
            select(func.coalesce(func.sum(ExpectationAllocation.amount), 0)).where(
                ExpectationAllocation.expectation_id == expectation_id,
                ExpectationAllocation.role == role,
                ExpectationAllocation.status.in_(statuses),
            )
        ) or Decimal(0)
    return Decimal(out["out"]), Decimal(out["return"])


def set_expectation_status(db: Session, expectation: MoneyExpectation, status: str) -> None:
    """Her decision. "Not expected back" pauses its allocations so the payments are unknown again;
    undoing it restores them if nothing else has claimed those payments in the meantime."""
    was_paused = expectation.status == "not_expected"
    expectation.status = status
    allocations = list(
        db.scalars(
            select(ExpectationAllocation).where(
                ExpectationAllocation.expectation_id == expectation.id
            )
        )
    )
    if status == "not_expected":
        for item in allocations:
            if item.status == "confirmed":
                item.status = "paused"
    elif was_paused:
        for item in allocations:
            if item.status == "paused":
                transaction = db.get(Transaction, item.transaction_id)
                if _used(db, transaction.id) + item.amount > abs(transaction.amount):
                    raise AllocationError(
                        "Some of those payments were assigned to something else since."
                    )
                item.status = "confirmed"
    db.flush()


REMEMBERED_REASON = "Applied your remembered choice for this person"
UNDO_REASON = "Undid your remembered choice"


def _mark_expense(db: Session, user: int, row: Transaction, reason: str, source: str) -> None:
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
        reason=reason,
        decision_source=source,
    )


def remember_choice(db: Session, user: int, counterparty: Counterparty, choice: str) -> None:
    """Remember for payments added from now on. Everything that exists today is left alone."""
    newest = db.scalar(
        select(func.coalesce(func.max(Transaction.id), 0))
        .join(TransactionMeaning, TransactionMeaning.transaction_id == Transaction.id)
        .where(Transaction.user_id == user, TransactionMeaning.counterparty_id == counterparty.id)
    )
    preference = db.scalar(
        select(MotionPreference).where(
            MotionPreference.user_id == user, MotionPreference.counterparty_id == counterparty.id
        )
    )
    if preference is None:
        db.add(
            MotionPreference(
                user_id=user,
                counterparty_id=counterparty.id,
                choice=choice,
                after_transaction_id=newest,
            )
        )
    elif preference.choice != choice:
        preference.choice, preference.after_transaction_id = choice, newest
    db.flush()


def apply_preferences(db: Session, user: int) -> int:
    """Apply remembered "expense" choices to later payments, disclosed and audited. Idempotent.

    A remembered "expected back" is never applied here: it only shapes the question.
    """
    applied = 0
    for preference in db.scalars(
        select(MotionPreference).where(
            MotionPreference.user_id == user, MotionPreference.choice == "expense"
        )
    ):
        for row in eligible_outgoing(db, user, preference.counterparty_id):
            if row.id > preference.after_transaction_id:
                _mark_expense(db, user, row, REMEMBERED_REASON, "confirmed_rule")
                applied += 1
    return applied


def _applied_rows(db: Session, user: int, preference: MotionPreference) -> list[Transaction]:
    rows = []
    for row in db.scalars(
        _bank_transactions(db, user)
        .join(TransactionMeaning, TransactionMeaning.transaction_id == Transaction.id)
        .where(
            TransactionMeaning.counterparty_id == preference.counterparty_id,
            Transaction.id > preference.after_transaction_id,
        )
    ):
        last = db.scalar(
            select(TransactionMeaningChange)
            .where(TransactionMeaningChange.transaction_id == row.id)
            .order_by(TransactionMeaningChange.version.desc())
            .limit(1)
        )
        if last and last.reason == REMEMBERED_REASON and last.version == row.meaning.version:
            rows.append(row)
    return rows


def remembered_view(db: Session, user: int) -> list[dict]:
    names = dict(
        db.execute(
            select(Counterparty.id, Counterparty.display_name).where(Counterparty.user_id == user)
        ).all()
    )
    return [
        {
            "id": p.id,
            "counterparty_id": p.counterparty_id,
            "counterparty_name": names.get(p.counterparty_id, ""),
            "choice": p.choice,
            "applied_count": len(_applied_rows(db, user, p)) if p.choice == "expense" else 0,
        }
        for p in db.scalars(
            select(MotionPreference)
            .where(MotionPreference.user_id == user)
            .order_by(MotionPreference.id)
        )
    ]


def undo_preference(db: Session, user: int, preference: MotionPreference) -> int:
    """Put untouched automatic applications back to unexplained and stop remembering."""
    reverted = 0
    for row in _applied_rows(db, user, preference):
        meaning = row.meaning
        apply_meaning_change(
            db,
            transaction=row,
            actor_user_id=user,
            purpose=Purpose.OTHER,
            person_id=meaning.person_id,
            employer_id=meaning.employer_id,
            trip_id=meaning.trip_id,
            counterparty_id=meaning.counterparty_id,
            notes=meaning.notes,
            reason=UNDO_REASON,
            decision_source="unresolved",
        )
        reverted += 1
    db.delete(preference)
    db.flush()
    return reverted


def answer_question(
    db: Session,
    user: int,
    counterparty: Counterparty,
    choice: str,
    transaction_ids: list[int] | None,
    remember: bool = False,
) -> int:
    """Apply her answer to the still-unanswered payments. Replays find nothing left to do."""
    rows = eligible_outgoing(db, user, counterparty.id)
    if transaction_ids is not None:
        rows = [row for row in rows if row.id in set(transaction_ids)]
    if remember:
        remember_choice(db, user, counterparty, choice)
    if not rows:
        return 0
    if choice == "expense":
        for row in rows:
            _mark_expense(db, user, row, "You said this payment was an expense", "user_confirmed")
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
    people = dict(db.execute(select(Person.id, Person.name).where(Person.user_id == user_id)).all())
    for expectation in db.scalars(
        select(MoneyExpectation)
        .where(MoneyExpectation.user_id == user_id)
        .order_by(MoneyExpectation.opened_on, MoneyExpectation.id)
    ):
        shown = ("confirmed", "paused") if expectation.status == "not_expected" else ("confirmed",)
        sent, returned = _totals(db, expectation.id, shown)
        if sent == 0 and returned == 0:
            continue
        allocations = db.execute(
            select(ExpectationAllocation.role, ExpectationAllocation.transaction_id)
            .join(Transaction, Transaction.id == ExpectationAllocation.transaction_id)
            .where(
                ExpectationAllocation.expectation_id == expectation.id,
                ExpectationAllocation.status.in_(shown),
            )
            .order_by(Transaction.txn_date, Transaction.id)
        ).all()
        result.append(
            {
                "expectation_id": expectation.id,
                "counterparty_id": expectation.counterparty_id,
                "counterparty_name": names.get(expectation.counterparty_id)
                or people.get(expectation.person_id, ""),
                "from_loans": expectation.person_id is not None
                and expectation.counterparty_id is None,
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
    people = dict(db.execute(select(Person.id, Person.name).where(Person.user_id == user_id)).all())
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
            by_person = (
                expectation.person_id is not None
                and meaning is not None
                and meaning.person_id == expectation.person_id
                and meaning.purpose == Purpose.LOAN_REPAYMENT
            )
            by_counterparty = (
                expectation.counterparty_id is not None
                and meaning is not None
                and meaning.counterparty_id == expectation.counterparty_id
            )
            if (
                item.id in skip
                or meaning is None
                or not (by_person or by_counterparty)
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
                    "counterparty_name": meaning.counterparty.display_name
                    if meaning.counterparty
                    else people.get(expectation.person_id, ""),
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


def _loan_rows(db: Session, user_id: int, person_id: int | None = None):
    skip_out = allocated_ids(db, user_id, "out", ("tracking", "written_off"))
    skip_back = allocated_ids(db, user_id, "return")
    loans, repayments = {}, {}
    for item in db.scalars(
        _bank_transactions(db, user_id).order_by(Transaction.txn_date, Transaction.id)
    ):
        meaning = item.meaning
        if meaning is None or meaning.person_id is None:
            continue
        if person_id is not None and meaning.person_id != person_id:
            continue
        if meaning.purpose == Purpose.LOAN_GIVEN and item.amount < 0 and item.id not in skip_out:
            loans.setdefault(meaning.person_id, []).append(item)
        elif (
            meaning.purpose == Purpose.LOAN_REPAYMENT
            and item.amount > 0
            and item.id not in skip_back
        ):
            repayments.setdefault(meaning.person_id, []).append(item)
    return loans, repayments


def loan_proposals(db: Session, user_id: int) -> list[dict]:
    """Existing loans proposed as expectations, grouped by person. Nothing is converted here."""
    loans, repayments = _loan_rows(db, user_id)
    result = []
    for person in db.scalars(
        select(Person).where(Person.user_id == user_id, Person.id.in_(list(loans)))
    ):
        lent, repaid = loans[person.id], repayments.get(person.id, [])
        sent = sum((-r.amount for r in lent), Decimal(0))
        back = sum((r.amount for r in repaid), Decimal(0))
        balance = person_balances(db, person)[1]
        result.append(
            {
                "person_id": person.id,
                "person_name": person.name,
                "loans": {
                    "count": len(lent),
                    "total": money(sent),
                    "evidence": [f"txn:{r.id}" for r in lent],
                },
                "repayments": {
                    "count": len(repaid),
                    "total": money(back),
                    "evidence": [f"txn:{r.id}" for r in repaid],
                },
                "loan_balance_now": money(balance),
                "would_track": {"sent": money(sent), "returned": money(min(back, sent))},
            }
        )
    return sorted(result, key=lambda item: item["person_name"])


def accept_loans(db: Session, user_id: int, person: Person) -> int:
    """Track a person's loans as an expectation. Purposes are untouched; returns that do not fit
    what was lent are left as they were. Replays find nothing left to map."""
    loans, repayments = _loan_rows(db, user_id, person.id)
    lent = loans.get(person.id, [])
    if not lent:
        return 0
    expectation = db.scalar(
        select(MoneyExpectation).where(
            MoneyExpectation.user_id == user_id,
            MoneyExpectation.person_id == person.id,
            MoneyExpectation.counterparty_id.is_(None),
            MoneyExpectation.status == "tracking",
        )
    )
    if expectation is None:
        expectation = MoneyExpectation(
            user_id=user_id,
            kind="person",
            person_id=person.id,
            status="tracking",
            opened_on=lent[0].txn_date,
        )
        db.add(expectation)
        db.flush()
    for row in lent:
        allocate(db, user_id, expectation, row, "out", -row.amount)
    skipped = 0
    for row in repayments.get(person.id, []):
        sent, returned = _totals(db, expectation.id)
        room = min(row.amount - _used(db, row.id), sent - returned)
        if room < row.amount:
            skipped += 1  # does not fully fit what was lent: left exactly as it was
            continue
        allocate(db, user_id, expectation, row, "return", row.amount)
    return skipped


def revert_loans(db: Session, expectation: MoneyExpectation) -> None:
    for item in db.scalars(
        select(ExpectationAllocation).where(
            ExpectationAllocation.expectation_id == expectation.id,
            ExpectationAllocation.status.in_(("confirmed", "paused")),
        )
    ):
        item.status = "reverted"
    db.flush()


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
        if row["status"] == "not_expected":
            continue  # unknown again: it is asked about, not reported as out
        if row["status"] == "written_off":
            text = (
                f"Written off (not counted): {_rupees(Decimal(row['sent']))} to "
                f"{row['counterparty_name']} since {date_label(date.fromisoformat(row['since']))}"
            )
        parts.append(text + ".")
        evidence += row["evidence"] + row["returns"]
    return " ".join(parts), evidence


_ = timedelta
