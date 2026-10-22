from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Counterparty,
    Transaction,
    TransactionGroup,
    TransactionGroupMember,
    TransactionMeaning,
    User,
)
from app.schemas import CounterpartyTransaction, TransactionGroupDetail, TransactionGroupSummary
from app.security import current_user

router = APIRouter(prefix="/transaction-groups", tags=["transaction groups"])


def _summary_query(user_id: int):
    return (
        select(
            TransactionGroup,
            Counterparty.display_name,
            func.coalesce(func.sum(func.abs(Transaction.amount)), 0).label("total"),
            func.count(Transaction.id).label("transaction_count"),
        )
        .join(Counterparty, Counterparty.id == TransactionGroup.counterparty_id)
        .outerjoin(
            TransactionGroupMember,
            TransactionGroupMember.group_id == TransactionGroup.id,
        )
        .outerjoin(Transaction, Transaction.id == TransactionGroupMember.transaction_id)
        .where(TransactionGroup.user_id == user_id, Counterparty.user_id == user_id)
        .group_by(TransactionGroup.id, Counterparty.display_name)
    )


def _summary(row) -> TransactionGroupSummary:
    group, counterparty_name, total, count = row
    return TransactionGroupSummary(
        id=group.id,
        counterparty_id=group.counterparty_id,
        counterparty_name=counterparty_name,
        purpose=group.purpose,
        direction=group.direction,
        total=Decimal(total),
        transaction_count=count,
    )


@router.get("", response_model=list[TransactionGroupSummary])
def list_transaction_groups(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.execute(
        _summary_query(user.id).order_by(
            func.count(Transaction.id).desc(), Counterparty.display_name
        )
    ).all()
    return [_summary(row) for row in rows if row[3] > 0]


@router.get("/{group_id}", response_model=TransactionGroupDetail)
def transaction_group_detail(
    group_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    summary_row = db.execute(
        _summary_query(user.id).where(TransactionGroup.id == group_id)
    ).one_or_none()
    if not summary_row:
        raise HTTPException(status_code=404, detail="Transaction group not found")
    rows = db.execute(
        select(Transaction, TransactionMeaning)
        .join(TransactionGroupMember, TransactionGroupMember.transaction_id == Transaction.id)
        .join(TransactionMeaning, TransactionMeaning.transaction_id == Transaction.id)
        .where(
            TransactionGroupMember.group_id == group_id,
            TransactionGroupMember.user_id == user.id,
            Transaction.user_id == user.id,
        )
        .order_by(Transaction.txn_date.desc(), Transaction.id.desc())
    ).all()
    return TransactionGroupDetail(
        group=_summary(summary_row),
        transactions=[
            CounterpartyTransaction(
                id=transaction.id,
                txn_date=transaction.txn_date,
                description=transaction.description,
                amount=transaction.amount,
                purpose=meaning.purpose,
            )
            for transaction, meaning in rows
        ],
    )
