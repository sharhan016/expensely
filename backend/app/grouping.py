from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Transaction, TransactionGroup, TransactionGroupMember


def _group_key(transaction: Transaction) -> str:
    meaning = transaction.meaning
    direction = "paid" if transaction.amount < 0 else "received"
    return f"{transaction.user_id}:{meaning.counterparty_id}:{meaning.purpose.value}:{direction}"


def sync_transaction_group(db: Session, transaction: Transaction) -> TransactionGroup | None:
    membership = db.scalar(
        select(TransactionGroupMember).where(
            TransactionGroupMember.transaction_id == transaction.id,
            TransactionGroupMember.user_id == transaction.user_id,
        )
    )
    meaning = transaction.meaning
    if not meaning or meaning.counterparty_id is None:
        if membership:
            db.delete(membership)
        return None

    key = _group_key(transaction)
    if membership and membership.group.group_key == key:
        return membership.group
    if membership:
        db.delete(membership)
        db.flush()

    direction = "paid" if transaction.amount < 0 else "received"
    group = db.scalar(
        select(TransactionGroup).where(
            TransactionGroup.user_id == transaction.user_id,
            TransactionGroup.group_key == key,
        )
    )
    if not group:
        group = TransactionGroup(
            user_id=transaction.user_id,
            counterparty_id=meaning.counterparty_id,
            purpose=meaning.purpose,
            direction=direction,
            group_key=key,
        )
        db.add(group)
        db.flush()
    db.add(
        TransactionGroupMember(
            user_id=transaction.user_id,
            group_id=group.id,
            transaction_id=transaction.id,
        )
    )
    return group
