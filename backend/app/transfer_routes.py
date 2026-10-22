from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased, selectinload

from app.database import get_db
from app.meanings import apply_meaning_change
from app.models import (
    Account,
    Purpose,
    Transaction,
    TransactionLink,
    User,
)
from app.schemas import (
    CardPaymentLinkResponse,
    TransactionLinkDecision,
    UnmatchedCardPaymentResponse,
)
from app.security import current_user
from app.transfer_matching import is_card_payment_credit

router = APIRouter(prefix="/transaction-links", tags=["transaction links"])


def _card_payment_rows(db: Session, user_id: int):
    outflow = aliased(Transaction)
    inflow = aliased(Transaction)
    bank_account = aliased(Account)
    card_account = aliased(Account)
    return db.execute(
        select(TransactionLink, outflow, inflow, bank_account.name, card_account.name)
        .join(outflow, outflow.id == TransactionLink.outflow_transaction_id)
        .join(inflow, inflow.id == TransactionLink.inflow_transaction_id)
        .join(bank_account, bank_account.id == outflow.account_id)
        .join(card_account, card_account.id == inflow.account_id)
        .where(
            TransactionLink.user_id == user_id,
            outflow.user_id == user_id,
            inflow.user_id == user_id,
            TransactionLink.link_type == "card_payment",
        )
        .order_by(inflow.txn_date.desc(), TransactionLink.id.desc())
    ).all()


def _response(row) -> CardPaymentLinkResponse:
    link, outflow, inflow, bank_name, card_name = row
    return CardPaymentLinkResponse(
        id=link.id,
        status=link.status,
        confidence=link.confidence,
        decision_source=link.decision_source,
        amount=inflow.amount,
        outflow_transaction_id=outflow.id,
        inflow_transaction_id=inflow.id,
        bank_account_name=bank_name,
        card_account_name=card_name,
        bank_date=outflow.txn_date,
        card_date=inflow.txn_date,
    )


@router.get("", response_model=list[CardPaymentLinkResponse])
def list_transaction_links(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [_response(row) for row in _card_payment_rows(db, user.id)]


@router.get("/unmatched-card-payments", response_model=list[UnmatchedCardPaymentResponse])
def unmatched_card_payments(db: Session = Depends(get_db), user: User = Depends(current_user)):
    linked_ids = select(TransactionLink.inflow_transaction_id).where(
        TransactionLink.user_id == user.id,
        TransactionLink.link_type == "card_payment",
        TransactionLink.status == "confirmed",
    )
    rows = db.execute(
        select(Transaction, Account.name)
        .join(Account, Account.id == Transaction.account_id)
        .where(
            Transaction.user_id == user.id,
            Account.user_id == user.id,
            Account.kind == "credit_card",
            Transaction.amount > 0,
            Transaction.id.not_in(linked_ids),
        )
        .order_by(Transaction.txn_date.desc(), Transaction.id.desc())
    ).all()
    return [
        UnmatchedCardPaymentResponse(
            transaction_id=transaction.id,
            amount=transaction.amount,
            txn_date=transaction.txn_date,
            card_account_name=account_name,
            reason="No unique owned-bank debit matched this payment",
        )
        for transaction, account_name in rows
        if is_card_payment_credit(transaction)
    ]


@router.patch("/{link_id}", response_model=CardPaymentLinkResponse)
def decide_transaction_link(
    link_id: int,
    payload: TransactionLinkDecision,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    link = db.scalar(
        select(TransactionLink)
        .options(
            selectinload(TransactionLink.outflow_transaction).selectinload(Transaction.meaning),
            selectinload(TransactionLink.inflow_transaction).selectinload(Transaction.meaning),
        )
        .where(TransactionLink.id == link_id, TransactionLink.user_id == user.id)
    )
    if not link:
        raise HTTPException(status_code=404, detail="Transaction link not found")
    if link.status != "confirmed":
        raise HTTPException(status_code=409, detail="This transaction link is already resolved")
    if payload.status == "rejected":
        prior = (
            (
                link.outflow_transaction,
                link.evidence.get("previous_outflow_purpose", Purpose.OTHER.value),
            ),
            (
                link.inflow_transaction,
                link.evidence.get("previous_inflow_purpose", Purpose.OTHER.value),
            ),
        )
        for transaction, prior_purpose in prior:
            meaning = transaction.meaning
            if meaning and meaning.purpose == Purpose.TRANSFER:
                apply_meaning_change(
                    db,
                    transaction=transaction,
                    actor_user_id=user.id,
                    purpose=Purpose(prior_purpose),
                    person_id=meaning.person_id,
                    employer_id=meaning.employer_id,
                    trip_id=meaning.trip_id,
                    counterparty_id=meaning.counterparty_id,
                    notes=meaning.notes,
                    reason="Rejected the automatic credit-card payment link",
                )
        link.status = "rejected"
        link.decision_source = "user_rejected"
    db.commit()
    row = next(row for row in _card_payment_rows(db, user.id) if row[0].id == link.id)
    return _response(row)
