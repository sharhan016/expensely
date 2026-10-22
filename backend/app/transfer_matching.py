import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.meanings import apply_meaning_change, ensure_meaning
from app.models import Account, Purpose, Transaction, TransactionLink, TransactionMeaning

_CARD_CREDIT_SIGNAL = re.compile(
    r"\b(?:PAYMENT\s+RECEIVED|PAYMENT\s+THANK\s+YOU|CARD\s+PAYMENT|AUTOPAY\s+PAYMENT)\b",
    re.I,
)
_BANK_DEBIT_SIGNAL = re.compile(
    r"\b(?:CREDIT\s*CARD|CARD\s+PAYMENT|CC\s+PAYMENT|AUTOPAY|CRED)\b",
    re.I,
)


def is_card_payment_credit(transaction: Transaction) -> bool:
    return (
        transaction.amount > 0 and _CARD_CREDIT_SIGNAL.search(transaction.description) is not None
    )


def _eligible_purpose(transaction: Transaction) -> bool:
    purpose = transaction.meaning.purpose if transaction.meaning else transaction.purpose
    return purpose in {Purpose.OTHER, Purpose.TRANSFER}


def _mark_as_transfer(db: Session, transaction: Transaction, user_id: int) -> str:
    meaning = ensure_meaning(db, transaction)
    previous = meaning.purpose.value
    if meaning.purpose == Purpose.OTHER:
        apply_meaning_change(
            db,
            transaction=transaction,
            actor_user_id=user_id,
            purpose=Purpose.TRANSFER,
            person_id=meaning.person_id,
            employer_id=meaning.employer_id,
            trip_id=meaning.trip_id,
            counterparty_id=meaning.counterparty_id,
            notes=meaning.notes,
            reason="Automatically linked the matching credit-card payment",
        )
    return previous


def match_card_payments(db: Session, user_id: int) -> list[TransactionLink]:
    existing_links = db.scalars(
        select(TransactionLink).where(TransactionLink.user_id == user_id)
    ).all()
    active_outflows = {
        link.outflow_transaction_id for link in existing_links if link.status == "confirmed"
    }
    active_inflows = {
        link.inflow_transaction_id for link in existing_links if link.status == "confirmed"
    }
    existing_pairs = {
        (link.outflow_transaction_id, link.inflow_transaction_id) for link in existing_links
    }
    load_meaning = selectinload(Transaction.meaning).selectinload(TransactionMeaning.counterparty)
    card_credits = db.scalars(
        select(Transaction)
        .join(Account, Account.id == Transaction.account_id)
        .options(load_meaning)
        .where(
            Transaction.user_id == user_id,
            Account.user_id == user_id,
            Account.kind == "credit_card",
            Transaction.amount > 0,
        )
    ).all()
    bank_debits = db.scalars(
        select(Transaction)
        .join(Account, Account.id == Transaction.account_id)
        .options(load_meaning)
        .where(
            Transaction.user_id == user_id,
            Account.user_id == user_id,
            Account.kind != "credit_card",
            Transaction.amount < 0,
        )
    ).all()
    candidate_pairs: list[tuple[Transaction, Transaction, dict[str, int | bool]]] = []
    for card_credit in card_credits:
        if (
            card_credit.id in active_inflows
            or not is_card_payment_credit(card_credit)
            or not _eligible_purpose(card_credit)
        ):
            continue
        card_account = card_credit.account
        card_hint = card_account.account_hint[-4:].casefold() if card_account.account_hint else ""
        for bank_debit in bank_debits:
            if (
                bank_debit.id in active_outflows
                or (bank_debit.id, card_credit.id) in existing_pairs
                or not _BANK_DEBIT_SIGNAL.search(bank_debit.description)
                or not _eligible_purpose(bank_debit)
                or -bank_debit.amount != card_credit.amount
            ):
                continue
            date_distance = abs((card_credit.txn_date - bank_debit.txn_date).days)
            if date_distance > 3:
                continue
            reference_match = bool(
                card_credit.reference
                and bank_debit.reference
                and card_credit.reference.casefold() == bank_debit.reference.casefold()
            )
            candidate_pairs.append(
                (
                    bank_debit,
                    card_credit,
                    {
                        "date_distance_days": date_distance,
                        "reference_match": reference_match,
                        "card_hint_match": bool(
                            card_hint and card_hint in bank_debit.description.casefold()
                        ),
                    },
                )
            )
    outflow_counts = Counter(pair[0].id for pair in candidate_pairs)
    inflow_counts = Counter(pair[1].id for pair in candidate_pairs)
    created = []
    for outflow, inflow, evidence in candidate_pairs:
        if outflow_counts[outflow.id] != 1 or inflow_counts[inflow.id] != 1:
            continue
        previous_outflow = _mark_as_transfer(db, outflow, user_id)
        previous_inflow = _mark_as_transfer(db, inflow, user_id)
        link = TransactionLink(
            user_id=user_id,
            link_type="card_payment",
            outflow_transaction_id=outflow.id,
            inflow_transaction_id=inflow.id,
            status="confirmed",
            confidence="high",
            decision_source="automatic_rule",
            evidence={
                **evidence,
                "previous_outflow_purpose": previous_outflow,
                "previous_inflow_purpose": previous_inflow,
            },
        )
        db.add(link)
        created.append(link)
    if created:
        db.flush()
    return created
