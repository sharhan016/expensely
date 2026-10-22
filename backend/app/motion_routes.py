from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import money_in_motion as motion
from app.database import get_db
from app.models import (
    Account,
    Counterparty,
    ExpectationAllocation,
    InstrumentType,
    MoneyExpectation,
    MotionPreference,
    Person,
    Transaction,
    TransactionLink,
    User,
)
from app.security import current_user

router = APIRouter(prefix="/motion", tags=["money in motion"])


class AnswerBody(BaseModel):
    counterparty_id: int
    answer: Literal["expected_back", "expense"]
    transaction_ids: list[int] | None = Field(default=None, max_length=500)
    remember: bool = False  # off unless she asks: remembers the choice for later payments only


class ReturnBody(BaseModel):
    expectation_id: int
    transaction_id: int
    amount: Decimal | None = Field(default=None, gt=0, max_digits=16, decimal_places=2)


class DismissBody(BaseModel):
    expectation_id: int
    transaction_id: int


class StatusBody(BaseModel):
    status: Literal["written_off", "not_expected", "open"]


class LoanBody(BaseModel):
    person_id: int


class TransferBody(BaseModel):
    outflow_id: int
    inflow_id: int


def _not_found():
    return HTTPException(status_code=404, detail="Not found")


def _expectation(db: Session, expectation_id: int, user_id: int) -> MoneyExpectation:
    item = db.scalar(
        select(MoneyExpectation).where(
            MoneyExpectation.id == expectation_id, MoneyExpectation.user_id == user_id
        )
    )
    if not item:
        raise _not_found()
    return item


def _transaction(db: Session, transaction_id: int, user_id: int) -> Transaction:
    item = db.scalar(
        select(Transaction).where(Transaction.id == transaction_id, Transaction.user_id == user_id)
    )
    if not item:
        raise _not_found()
    return item


def _view(db: Session, user_id: int) -> dict:
    motion.sync_own_transfers(db, user_id)
    motion.apply_preferences(db, user_id)
    db.commit()
    return {
        "remembered": motion.remembered_view(db, user_id),
        "loan_proposals": motion.loan_proposals(db, user_id),
        "questions": motion.pending_questions(db, user_id),
        "outstanding": motion.outstanding(db, user_id),
        "return_proposals": motion.return_proposals(db, user_id),
        "transfer_proposals": [
            item for item in motion.own_transfer_candidates(db, user_id) if not item["strong"]
        ],
    }


@router.get("")
def state(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _view(db, user.id)


@router.post("/answers")
def answer(body: AnswerBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    counterparty = db.scalar(
        select(Counterparty).where(
            Counterparty.id == body.counterparty_id, Counterparty.user_id == user.id
        )
    )
    if not counterparty:
        raise _not_found()
    for transaction_id in body.transaction_ids or []:
        _transaction(db, transaction_id, user.id)  # a foreign id is a 404, never skipped quietly
    try:
        motion.answer_question(
            db, user.id, counterparty, body.answer, body.transaction_ids, body.remember
        )
        db.commit()
    except motion.AllocationError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _view(db, user.id)


@router.post("/returns")
def confirm_return(
    body: ReturnBody, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    expectation = _expectation(db, body.expectation_id, user.id)
    transaction = _transaction(db, body.transaction_id, user.id)
    try:
        motion.confirm_return(db, user.id, expectation, transaction, body.amount)
        db.commit()
    except motion.AllocationError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _view(db, user.id)


@router.post("/returns/dismiss")
def dismiss(body: DismissBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    expectation = _expectation(db, body.expectation_id, user.id)
    motion.dismiss_return(db, user.id, expectation, _transaction(db, body.transaction_id, user.id))
    db.commit()
    return _view(db, user.id)


@router.post("/allocations/{allocation_id}/revert")
def revert(allocation_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    allocation = db.scalar(
        select(ExpectationAllocation).where(
            ExpectationAllocation.id == allocation_id, ExpectationAllocation.user_id == user.id
        )
    )
    if not allocation:
        raise _not_found()
    if allocation.status == "confirmed":
        allocation.status = "reverted"
        db.commit()
    return _view(db, user.id)


@router.post("/expectations/{expectation_id}/status")
def set_status(
    expectation_id: int,
    body: StatusBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    expectation = _expectation(db, expectation_id, user.id)
    try:
        motion.set_expectation_status(
            db, expectation, "tracking" if body.status == "open" else body.status
        )
        db.commit()
    except motion.AllocationError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _view(db, user.id)


@router.post("/transfers")
def confirm_transfer(
    body: TransferBody, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    out = _transaction(db, body.outflow_id, user.id)
    inn = _transaction(db, body.inflow_id, user.id)
    accounts = {
        a.id: a
        for a in db.scalars(select(Account).where(Account.id.in_({out.account_id, inn.account_id})))
    }
    if (
        out.amount >= 0
        or inn.amount <= 0
        or abs(out.amount) != inn.amount
        or out.account_id == inn.account_id
        or any(a.instrument_type == InstrumentType.CREDIT_CARD for a in accounts.values())
    ):
        raise HTTPException(status_code=409, detail="These two movements cannot be one transfer")
    link = db.scalar(
        select(TransactionLink).where(
            TransactionLink.user_id == user.id,
            TransactionLink.outflow_transaction_id == out.id,
            TransactionLink.inflow_transaction_id == inn.id,
        )
    )
    if link is None:
        db.add(
            TransactionLink(
                user_id=user.id,
                link_type="own_transfer",
                outflow_transaction_id=out.id,
                inflow_transaction_id=inn.id,
                status="confirmed",
                confidence="high",
                decision_source="user_confirmed",
                evidence={"basis": "confirmed by you"},
            )
        )
    elif link.status != "confirmed":
        link.status, link.decision_source = "confirmed", "user_confirmed"
    db.commit()
    return _view(db, user.id)


def _preference(db: Session, preference_id: int, user_id: int) -> MotionPreference:
    item = db.scalar(
        select(MotionPreference).where(
            MotionPreference.id == preference_id, MotionPreference.user_id == user_id
        )
    )
    if not item:
        raise _not_found()
    return item


@router.delete("/preferences/{preference_id}")
def forget(preference_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Stop remembering. Payments already handled are left exactly as they are."""
    db.delete(_preference(db, preference_id, user.id))
    db.commit()
    return _view(db, user.id)


@router.post("/preferences/{preference_id}/undo")
def undo(preference_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    motion.undo_preference(db, user.id, _preference(db, preference_id, user.id))
    db.commit()
    return _view(db, user.id)


@router.post("/loans/accept")
def accept_loans(body: LoanBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    person = db.scalar(select(Person).where(Person.id == body.person_id, Person.user_id == user.id))
    if not person:
        raise _not_found()
    try:
        skipped = motion.accept_loans(db, user.id, person)
        db.commit()
    except motion.AllocationError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**_view(db, user.id), "loan_skipped": skipped}


@router.post("/loans/{expectation_id}/revert")
def revert_loans(
    expectation_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    expectation = _expectation(db, expectation_id, user.id)
    if expectation.person_id is None or expectation.counterparty_id is not None:
        raise _not_found()
    motion.revert_loans(db, expectation)
    db.commit()
    return _view(db, user.id)
