import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant_routes import post_import_summary
from app.coverage import date_label
from app.coverage_service import coverage_report
from app.database import get_db
from app.imports import unlock_statement
from app.models import (
    AcknowledgedGap,
    ReviewPeriod,
    StatementImport,
    User,
    WorkflowRun,
)
from app.parsers import ParseError
from app.security import current_user
from app.workflow import service

router = APIRouter(tags=["assistant workflow"])


class PeriodBody(BaseModel):
    period_start: date
    period_end: date


class RunCreate(BaseModel):
    import_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    client_key: str | None = Field(default=None, max_length=64)


class AnswerBody(BaseModel):
    answer: str
    period_start: date | None = None
    period_end: date | None = None
    choice: str | None = None
    account_id: int | None = None
    gap_start: date | None = None
    gap_end: date | None = None


class PasswordBody(BaseModel):
    import_id: uuid.UUID
    password: SecretStr


def _owned_run(db: Session, run_id: uuid.UUID, user_id: int) -> WorkflowRun:
    run = db.scalar(
        select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.user_id == user_id)
    )
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def _period_json(period: ReviewPeriod | None):
    if not period:
        return None
    return {
        "period_start": period.period_start.isoformat(),
        "period_end": period.period_end.isoformat(),
    }


def _validate(body: PeriodBody) -> None:
    if body.period_end < body.period_start:
        raise HTTPException(status_code=422, detail="The last date must not be before the first")


def _save_period(db: Session, user_id: int, start: date, end: date) -> ReviewPeriod:
    period = db.scalar(select(ReviewPeriod).where(ReviewPeriod.user_id == user_id))
    if not period:
        period = ReviewPeriod(user_id=user_id, period_start=start, period_end=end)
        db.add(period)
    period.period_start, period.period_end = start, end
    db.flush()
    return period


@router.get("/review-period")
def get_review_period(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _period_json(db.scalar(select(ReviewPeriod).where(ReviewPeriod.user_id == user.id)))


@router.put("/review-period")
def put_review_period(
    body: PeriodBody, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    _validate(body)
    period = _save_period(db, user.id, body.period_start, body.period_end)
    db.commit()
    return _period_json(period)


def _greeting(db: Session, user_id: int, period: ReviewPeriod | None) -> str:
    if period is None:
        return (
            "Hello. Which period do you want to understand? Tell me the first and last date, then "
            "send me the statements that cover it. I can only tell you about dates those "
            "statements cover."
        )
    report = coverage_report(db, user_id)
    if not report["accounts"]:
        return (
            f"You are looking at {date_label(period.period_start)} to "
            f"{date_label(period.period_end)}. Send me the statements that cover it. I can only "
            "tell you about dates they cover."
        )
    through = report["overall"]["reconciled_through"]
    text = (
        f"Welcome back. You are looking at {date_label(period.period_start)} to "
        f"{date_label(period.period_end)}. {report['overall']['banner']}"
    )
    if through:
        through_date = date.fromisoformat(through)
        text += f" Send me anything after {date_label(through_date)}."
        if through_date >= period.period_end:
            text += " Your whole review period is covered."
    else:
        text += " Send me the statements for your review period."
    return text


@router.get("/workflow/state")
def workflow_state(db: Session = Depends(get_db), user: User = Depends(current_user)):
    period = db.scalar(select(ReviewPeriod).where(ReviewPeriod.user_id == user.id))
    run = db.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.user_id == user.id, WorkflowRun.status == "waiting")
        .order_by(WorkflowRun.created_at.desc())
        .limit(1)
    )
    latest = db.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.user_id == user.id)
        .order_by(WorkflowRun.created_at.desc())
        .limit(1)
    )
    return {
        "review_period": _period_json(period),
        "greeting": _greeting(db, user.id, period),
        "run": service.run_view(db, run) if run else None,
        "latest_run": service.run_view(db, latest) if latest else None,
    }


@router.post("/workflow/runs", status_code=201)
def create_run(body: RunCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    ids = [str(item) for item in body.import_ids]
    owned = set(
        str(item)
        for item in db.scalars(
            select(StatementImport.id).where(
                StatementImport.user_id == user.id, StatementImport.id.in_(body.import_ids)
            )
        )
    )
    if owned != set(ids):
        raise HTTPException(status_code=404, detail="One or more statement imports were not found")
    run = service.start_run(db, user.id, ids, body.client_key)
    return service.run_view(db, run)


@router.get("/workflow/runs/{run_id}")
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return service.run_view(db, _owned_run(db, run_id, user.id))


@router.get("/workflow/runs/{run_id}/events")
def get_events(
    run_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    return service.run_view(db, _owned_run(db, run_id, user.id))["events"]


@router.post("/workflow/runs/{run_id}/answer")
def answer_run(
    run_id: uuid.UUID,
    body: AnswerBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    run = _owned_run(db, run_id, user.id)
    if run.status != "waiting":
        return service.run_view(db, run)  # a replayed or double-submitted answer is a no-op
    waiting = run.waiting_for or {}
    if body.answer != waiting.get("type"):
        raise HTTPException(status_code=409, detail="That question has already been answered")
    if body.answer == "period":
        if body.period_start is None or body.period_end is None:
            raise HTTPException(status_code=422, detail="Give the first and last date")
        _validate(PeriodBody(period_start=body.period_start, period_end=body.period_end))
        _save_period(db, user.id, body.period_start, body.period_end)
        code = {"answered": "period"}
    elif body.answer == "gap":
        if (
            body.account_id != waiting["account_id"]
            or body.gap_start is None
            or body.gap_end is None
            or body.gap_start.isoformat() != waiting["gap_start"]
            or body.gap_end.isoformat() != waiting["gap_end"]
        ):
            raise HTTPException(status_code=409, detail="That question has already been answered")
        if body.choice not in {"not_needed", "will_upload"}:
            raise HTTPException(status_code=422, detail="Choose one of the offered answers")
        if body.choice == "not_needed" and not db.scalar(
            select(AcknowledgedGap.id).where(
                AcknowledgedGap.account_id == body.account_id,
                AcknowledgedGap.gap_start == body.gap_start,
                AcknowledgedGap.gap_end == body.gap_end,
            )
        ):
            db.add(
                AcknowledgedGap(
                    user_id=user.id,
                    account_id=body.account_id,
                    gap_start=body.gap_start,
                    gap_end=body.gap_end,
                )
            )
        db.flush()
        code = {"answered": "gap", "choice": body.choice}
    else:
        raise HTTPException(status_code=422, detail="Unknown answer")
    db.commit()
    service.resume_run(db, run, code)
    return service.run_view(db, run)


@router.post("/workflow/runs/{run_id}/password")
def password_for_run(
    run_id: uuid.UUID,
    body: PasswordBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    run = _owned_run(db, run_id, user.id)
    if run.status != "waiting":
        return service.run_view(db, run)
    waiting = run.waiting_for or {}
    if waiting.get("type") != "password" or waiting.get("import_id") != str(body.import_id):
        raise HTTPException(status_code=409, detail="That question has already been answered")
    statement = db.scalar(
        select(StatementImport).where(
            StatementImport.id == body.import_id, StatementImport.user_id == user.id
        )
    )
    if not statement:
        raise HTTPException(status_code=404, detail="Statement import not found")
    try:
        unlock_statement(db, statement, body.password.get_secret_value(), user.id)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail="The password did not unlock this PDF") from exc
    service.resume_run(db, run, {"answered": "password"})
    if run.status == "completed" and run.import_ids:
        post_import_summary(db, user.id, run)
    return service.run_view(db, run)
