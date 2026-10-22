from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.meanings import apply_meaning_change
from app.models import AgentAction, Purpose, Transaction, User, utc_now
from app.schemas import AgentActionDecision, AgentActionResponse
from app.security import current_user

router = APIRouter(prefix="/agent-actions", tags=["agent actions"])


def _owned_action(db: Session, action_id: int, user_id: int) -> AgentAction:
    action = db.scalar(
        select(AgentAction).where(AgentAction.id == action_id, AgentAction.user_id == user_id)
    )
    if not action:
        raise HTTPException(status_code=404, detail="Agent action not found")
    return action


def _proposal_transactions(db: Session, action: AgentAction) -> list[Transaction]:
    transaction_ids = action.proposal.get("transaction_ids", [])
    rows = db.scalars(
        select(Transaction)
        .options(selectinload(Transaction.meaning))
        .where(
            Transaction.user_id == action.user_id,
            Transaction.id.in_(transaction_ids),
        )
        .order_by(Transaction.id)
    ).all()
    if len(rows) != len(transaction_ids):
        raise HTTPException(status_code=409, detail="The proposal no longer matches the ledger")
    return rows


@router.get("", response_model=list[AgentActionResponse])
def list_agent_actions(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return db.scalars(
        select(AgentAction)
        .where(AgentAction.user_id == user.id)
        .order_by(AgentAction.created_at.desc(), AgentAction.id.desc())
        .limit(100)
    ).all()


@router.patch("/{action_id}", response_model=AgentActionResponse)
def decide_agent_action(
    action_id: int,
    payload: AgentActionDecision,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    action = _owned_action(db, action_id, user.id)
    if action.action_type != "apply_counterparty_purpose":
        raise HTTPException(status_code=409, detail="This action type cannot be changed here")
    if payload.status == "rejected":
        if action.status != "proposed":
            raise HTTPException(status_code=409, detail="Only a proposed action can be rejected")
        action.status = "rejected"
        action.decision_source = "user_rejected"
        action.resolved_at = utc_now()
    elif payload.status == "accepted":
        if action.status != "proposed":
            raise HTTPException(status_code=409, detail="Only a proposed action can be accepted")
        transactions = _proposal_transactions(db, action)
        versions = action.proposal.get("versions", {})
        counterparty_id = int(action.target_id)
        for transaction in transactions:
            meaning = transaction.meaning
            if (
                not meaning
                or meaning.counterparty_id != counterparty_id
                or meaning.purpose != Purpose.OTHER
                or meaning.version != versions.get(str(transaction.id))
            ):
                raise HTTPException(
                    status_code=409,
                    detail="A proposed movement changed; review the flow again before applying",
                )
        purpose = Purpose(action.proposal["purpose"])
        applied_versions = {}
        for transaction in transactions:
            meaning = transaction.meaning
            updated = apply_meaning_change(
                db,
                transaction=transaction,
                actor_user_id=user.id,
                purpose=purpose,
                person_id=meaning.person_id,
                employer_id=meaning.employer_id,
                trip_id=meaning.trip_id,
                counterparty_id=meaning.counterparty_id,
                category=meaning.category,
                notes=meaning.notes,
                reason="Accepted retrospective counterparty-purpose proposal",
            )
            applied_versions[str(transaction.id)] = updated.version
        action.status = "accepted"
        action.evidence = {**action.evidence, "applied_versions": applied_versions}
        action.decision_source = "user_accepted"
        action.resolved_at = utc_now()
    else:
        if action.status != "accepted":
            raise HTTPException(status_code=409, detail="Only an accepted action can be reverted")
        transactions = _proposal_transactions(db, action)
        applied_versions = action.evidence.get("applied_versions", {})
        applied_purpose = Purpose(action.proposal["purpose"])
        for transaction in transactions:
            meaning = transaction.meaning
            if (
                not meaning
                or meaning.purpose != applied_purpose
                or meaning.version != applied_versions.get(str(transaction.id))
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A movement was corrected later, so this batch cannot be safely reverted"
                    ),
                )
        for transaction in transactions:
            meaning = transaction.meaning
            apply_meaning_change(
                db,
                transaction=transaction,
                actor_user_id=user.id,
                purpose=Purpose.OTHER,
                person_id=meaning.person_id,
                employer_id=meaning.employer_id,
                trip_id=meaning.trip_id,
                counterparty_id=meaning.counterparty_id,
                category=meaning.category,
                notes=meaning.notes,
                reason="Reverted retrospective counterparty-purpose proposal",
            )
        action.status = "reverted"
        action.decision_source = "user_reverted"
        action.resolved_at = utc_now()
    db.commit()
    db.refresh(action)
    return action
