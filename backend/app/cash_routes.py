from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cash import cash_summary
from app.database import get_db
from app.models import (
    Account,
    CashStatementEntry,
    CashWithdrawalMarker,
    InstrumentType,
    Transaction,
    User,
)
from app.security import current_user

router = APIRouter(prefix="/cash", tags=["cash in hand"])


class WithdrawalDecision(BaseModel):
    is_cash_withdrawal: bool


class CashEntryCreate(BaseModel):
    entry_date: date
    amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2)
    note: str = Field(default="", max_length=200)


@router.get("")
def cash(db: Session = Depends(get_db), user: User = Depends(current_user)):
    summary = cash_summary(db, user.id)
    db.commit()  # persists rule markers created while summarizing
    return summary


@router.put("/withdrawals/{transaction_id}")
def decide_withdrawal(
    transaction_id: int,
    payload: WithdrawalDecision,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    transaction = db.scalar(
        select(Transaction).where(Transaction.id == transaction_id, Transaction.user_id == user.id)
    )
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    account = db.get(Account, transaction.account_id)
    if transaction.amount >= 0 or account.instrument_type == InstrumentType.CREDIT_CARD:
        raise HTTPException(
            status_code=422, detail="Only a debit from a bank account can be a cash withdrawal"
        )
    marker = db.scalar(
        select(CashWithdrawalMarker).where(CashWithdrawalMarker.transaction_id == transaction.id)
    )
    if marker is None:
        marker = CashWithdrawalMarker(user_id=user.id, transaction_id=transaction.id)
        db.add(marker)
    marker.is_cash_withdrawal = payload.is_cash_withdrawal
    marker.source = "user"
    db.flush()
    summary = cash_summary(db, user.id)
    db.commit()
    return summary


@router.post("/entries", status_code=201)
def add_entry(
    payload: CashEntryCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    db.add(
        CashStatementEntry(
            user_id=user.id,
            entry_date=payload.entry_date,
            amount=payload.amount,
            note=payload.note.strip(),
        )
    )
    db.flush()
    summary = cash_summary(db, user.id)
    db.commit()
    return summary


@router.delete("/entries/{entry_id}", status_code=204)
def delete_entry(entry_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    entry = db.scalar(
        select(CashStatementEntry).where(
            CashStatementEntry.id == entry_id, CashStatementEntry.user_id == user.id
        )
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Cash entry not found")
    db.delete(entry)
    db.commit()
    return Response(status_code=204)
