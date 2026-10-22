from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import AgentAction, Counterparty, Purpose, Transaction, TransactionMeaning, User
from app.schemas import (
    AgentActionResponse,
    CounterpartyDetail,
    CounterpartyMeaningProposalCreate,
    CounterpartyPurposeTotal,
    CounterpartySummary,
    CounterpartyTransaction,
    CounterpartyUpdate,
)
from app.security import current_user

router = APIRouter(prefix="/counterparties", tags=["counterparty flows"])
_SAFE_DEFAULT_PURPOSES = {Purpose.PERSONAL, Purpose.INCOME, Purpose.REFUND}


def _summary_query(user_id: int):
    return (
        select(
            Counterparty,
            func.coalesce(
                func.sum(case((Transaction.amount < 0, -Transaction.amount), else_=0)), 0
            ).label("paid_total"),
            func.coalesce(
                func.sum(case((Transaction.amount > 0, Transaction.amount), else_=0)), 0
            ).label("received_total"),
            func.count(Transaction.id).label("transaction_count"),
            func.coalesce(
                func.sum(case((TransactionMeaning.purpose == Purpose.OTHER, 1), else_=0)), 0
            ).label("unresolved_count"),
        )
        .outerjoin(TransactionMeaning, TransactionMeaning.counterparty_id == Counterparty.id)
        .outerjoin(Transaction, Transaction.id == TransactionMeaning.transaction_id)
        .where(Counterparty.user_id == user_id)
        .group_by(Counterparty.id)
    )


def _to_summary(row) -> CounterpartySummary:
    counterparty, paid, received, count, unresolved = row
    return CounterpartySummary.model_validate(counterparty).model_copy(
        update={
            "paid_total": paid,
            "received_total": received,
            "transaction_count": count,
            "unresolved_count": unresolved,
        }
    )


@router.get("", response_model=list[CounterpartySummary])
def list_counterparties(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.execute(
        _summary_query(user.id).order_by(
            func.count(Transaction.id).desc(), Counterparty.display_name
        )
    ).all()
    return [_to_summary(row) for row in rows]


@router.get("/{counterparty_id}", response_model=CounterpartyDetail)
def counterparty_detail(
    counterparty_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    summary_row = db.execute(
        _summary_query(user.id).where(Counterparty.id == counterparty_id)
    ).one_or_none()
    if not summary_row:
        raise HTTPException(status_code=404, detail="Counterparty not found")
    rows = db.execute(
        select(Transaction, TransactionMeaning)
        .join(TransactionMeaning, TransactionMeaning.transaction_id == Transaction.id)
        .where(
            Transaction.user_id == user.id,
            TransactionMeaning.user_id == user.id,
            TransactionMeaning.counterparty_id == counterparty_id,
        )
        .order_by(Transaction.txn_date.desc(), Transaction.id.desc())
    ).all()
    totals: dict[Purpose, dict[str, Decimal | int]] = defaultdict(
        lambda: {
            "paid": Decimal("0"),
            "received": Decimal("0"),
            "transaction_count": 0,
        }
    )
    transactions = []
    for transaction, meaning in rows:
        if transaction.amount < 0:
            totals[meaning.purpose]["paid"] += -transaction.amount
        else:
            totals[meaning.purpose]["received"] += transaction.amount
        totals[meaning.purpose]["transaction_count"] += 1
        transactions.append(
            CounterpartyTransaction(
                id=transaction.id,
                txn_date=transaction.txn_date,
                description=transaction.description,
                amount=transaction.amount,
                purpose=meaning.purpose,
            )
        )
    purpose_totals = [
        CounterpartyPurposeTotal(
            purpose=purpose,
            paid=Decimal(values["paid"]),
            received=Decimal(values["received"]),
            transaction_count=int(values["transaction_count"]),
        )
        for purpose, values in sorted(totals.items(), key=lambda item: item[0].value)
    ]
    return CounterpartyDetail(
        counterparty=_to_summary(summary_row),
        purpose_totals=purpose_totals,
        transactions=transactions,
    )


@router.patch("/{counterparty_id}", response_model=CounterpartySummary)
def update_counterparty(
    counterparty_id: int,
    payload: CounterpartyUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    counterparty = db.scalar(
        select(Counterparty)
        .options(selectinload(Counterparty.aliases))
        .where(Counterparty.id == counterparty_id, Counterparty.user_id == user.id)
    )
    if not counterparty:
        raise HTTPException(status_code=404, detail="Counterparty not found")
    if payload.display_name is not None:
        counterparty.display_name = payload.display_name.strip()
    if payload.kind is not None:
        counterparty.kind = payload.kind
    if "default_purpose" in payload.model_fields_set:
        if (
            payload.default_purpose is not None
            and payload.default_purpose not in _SAFE_DEFAULT_PURPOSES
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Automatic counterparty rules support personal, income, and refund only; "
                    "review obligations and transfers individually"
                ),
            )
        counterparty.default_purpose = payload.default_purpose
    counterparty.is_confirmed = True
    for alias in counterparty.aliases:
        alias.is_confirmed = True
    db.commit()
    row = db.execute(_summary_query(user.id).where(Counterparty.id == counterparty.id)).one()
    return _to_summary(row)


@router.post(
    "/{counterparty_id}/meaning-proposals",
    response_model=AgentActionResponse,
    status_code=201,
)
def create_meaning_proposal(
    counterparty_id: int,
    payload: CounterpartyMeaningProposalCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    counterparty = db.scalar(
        select(Counterparty).where(
            Counterparty.id == counterparty_id,
            Counterparty.user_id == user.id,
        )
    )
    if not counterparty:
        raise HTTPException(status_code=404, detail="Counterparty not found")
    if payload.purpose not in _SAFE_DEFAULT_PURPOSES:
        raise HTTPException(
            status_code=422,
            detail="Obligations and transfers must be reviewed one movement at a time",
        )
    rows = db.execute(
        select(Transaction, TransactionMeaning)
        .join(TransactionMeaning, TransactionMeaning.transaction_id == Transaction.id)
        .where(
            Transaction.user_id == user.id,
            TransactionMeaning.user_id == user.id,
            TransactionMeaning.counterparty_id == counterparty.id,
            TransactionMeaning.purpose == Purpose.OTHER,
        )
        .order_by(Transaction.txn_date, Transaction.id)
    ).all()
    if not rows:
        raise HTTPException(status_code=409, detail="This flow has no unresolved past movements")
    transaction_ids = [transaction.id for transaction, _ in rows]
    versions = {str(transaction.id): meaning.version for transaction, meaning in rows}
    existing = db.scalars(
        select(AgentAction).where(
            AgentAction.user_id == user.id,
            AgentAction.action_type == "apply_counterparty_purpose",
            AgentAction.target_type == "counterparty",
            AgentAction.target_id == str(counterparty.id),
            AgentAction.status == "proposed",
        )
    ).all()
    for action in existing:
        if (
            action.proposal.get("purpose") == payload.purpose.value
            and action.proposal.get("transaction_ids") == transaction_ids
        ):
            return action
    action = AgentAction(
        user_id=user.id,
        action_type="apply_counterparty_purpose",
        target_type="counterparty",
        target_id=str(counterparty.id),
        status="proposed",
        proposal={
            "purpose": payload.purpose.value,
            "transaction_ids": transaction_ids,
            "versions": versions,
            "transaction_count": len(rows),
            "total": str(sum((abs(transaction.amount) for transaction, _ in rows), Decimal("0"))),
        },
        evidence={
            "counterparty_confirmed": counterparty.is_confirmed,
            "source": "explicit_retrospective_review",
        },
        decision_source="assistant_proposal",
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action
