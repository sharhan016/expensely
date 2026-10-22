from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.coverage_service import (
    account_entry,
    coverage_report,
    recompute_reconciliations,
)
from app.database import get_db
from app.models import Account, AcknowledgedGap, CoverageGapUnbridge, User
from app.security import current_user

router = APIRouter(prefix="/coverage", tags=["statement coverage"])


class AccountCoverageSettings(BaseModel):
    in_scope: bool | None = None
    # Send null to go back to "the first statement's start".
    records_start: date | None = None
    records_opening_balance: Decimal | None = None


class GapRef(BaseModel):
    gap_start: date
    gap_end: date


def _owned_account(db: Session, account_id: int, user_id: int) -> Account:
    account = db.scalar(select(Account).where(Account.id == account_id, Account.user_id == user_id))
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.get("")
def coverage(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return coverage_report(db, user.id)


@router.post("/recompute")
def recompute(db: Session = Depends(get_db), user: User = Depends(current_user)):
    result = recompute_reconciliations(db, user.id)
    db.commit()
    return {**result, "coverage": coverage_report(db, user.id)}


@router.patch("/accounts/{account_id}")
def update_settings(
    account_id: int,
    payload: AccountCoverageSettings,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    account = _owned_account(db, account_id, user.id)
    changes = payload.model_fields_set
    if "in_scope" in changes and payload.in_scope is not None:
        account.in_scope = payload.in_scope
    if "records_start" in changes:
        account.records_start = payload.records_start
        if payload.records_start is None:
            account.records_opening_balance = None
    if "records_opening_balance" in changes and payload.records_start is not None:
        account.records_opening_balance = payload.records_opening_balance
    db.commit()
    return coverage_report(db, user.id)


def _bridged_or_unbridged(db: Session, account: Account, gap: GapRef) -> bool:
    entry = account_entry(db, account)
    return any(
        segment.kind in {"bridged", "gap"}
        and (segment.start, segment.end) == (gap.gap_start, gap.gap_end)
        for segment in entry.coverage.segments
    )


@router.post("/accounts/{account_id}/unbridged-gaps")
def unbridge_gap(
    account_id: int,
    payload: GapRef,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    account = _owned_account(db, account_id, user.id)
    if not _bridged_or_unbridged(db, account, payload):
        raise HTTPException(status_code=422, detail="That is not a bridged gap on this account")
    exists = db.scalar(
        select(CoverageGapUnbridge.id).where(
            CoverageGapUnbridge.account_id == account.id,
            CoverageGapUnbridge.gap_start == payload.gap_start,
            CoverageGapUnbridge.gap_end == payload.gap_end,
        )
    )
    if not exists:
        db.add(
            CoverageGapUnbridge(
                user_id=user.id,
                account_id=account.id,
                gap_start=payload.gap_start,
                gap_end=payload.gap_end,
            )
        )
        db.commit()
    return coverage_report(db, user.id)


@router.delete("/accounts/{account_id}/unbridged-gaps", status_code=204)
def rebridge_gap(
    account_id: int,
    gap_start: date = Query(),
    gap_end: date = Query(),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    account = _owned_account(db, account_id, user.id)
    db.execute(
        delete(CoverageGapUnbridge).where(
            CoverageGapUnbridge.user_id == user.id,
            CoverageGapUnbridge.account_id == account.id,
            CoverageGapUnbridge.gap_start == gap_start,
            CoverageGapUnbridge.gap_end == gap_end,
        )
    )
    db.commit()
    return Response(status_code=204)


@router.delete("/accounts/{account_id}/acknowledged-gaps", status_code=204)
def take_back_acknowledged_gap(
    account_id: int,
    gap_start: date = Query(),
    gap_end: date = Query(),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    account = _owned_account(db, account_id, user.id)
    db.execute(
        delete(AcknowledgedGap).where(
            AcknowledgedGap.user_id == user.id,
            AcknowledgedGap.account_id == account.id,
            AcknowledgedGap.gap_start == gap_start,
            AcknowledgedGap.gap_end == gap_end,
        )
    )
    db.commit()
    return Response(status_code=204)
