from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.period_report import (
    BUCKETS,
    MAX_SPAN_DAYS,
    build_report,
    export_table,
    period_rows,
    row_json,
)
from app.security import current_user

router = APIRouter(prefix="/reports", tags=["period report"])


def _range(start: date, end: date) -> tuple[date, date]:
    if end < start:
        raise HTTPException(status_code=422, detail="The last date must not be before the first")
    if (end - start).days >= MAX_SPAN_DAYS:
        raise HTTPException(status_code=422, detail="Choose a period of up to a year")
    return start, end


@router.get("/period")
def period(
    start: date = Query(alias="from"),
    end: date = Query(alias="to"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    return build_report(db, user.id, *_range(start, end))


@router.get("/period/rows")
def rows(
    start: date = Query(alias="from"),
    end: date = Query(alias="to"),
    bucket: str | None = None,
    on: date | None = Query(default=None, alias="date"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if bucket is not None and bucket not in BUCKETS:
        raise HTTPException(status_code=422, detail="Unknown bucket")
    found = period_rows(db, user.id, *_range(start, end))
    return [
        row_json(row)
        for row in found
        if (bucket is None or row["bucket"] == bucket) and (on is None or row["txn_date"] == on)
    ]


def _export(start, end, db, user, delimiter, media, extension) -> Response:
    start, end = _range(start, end)
    body = export_table(period_rows(db, user.id, start, end), delimiter)
    return Response(
        content=body,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="report-{start}-to-{end}.{extension}"'
        },
    )


@router.get("/period.csv")
def period_csv(
    start: date = Query(alias="from"),
    end: date = Query(alias="to"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    return _export(start, end, db, user, ",", "text/csv; charset=utf-8", "csv")


@router.get("/period.tsv")
def period_tsv(
    start: date = Query(alias="from"),
    end: date = Query(alias="to"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    return _export(start, end, db, user, "\t", "text/tab-separated-values; charset=utf-8", "tsv")
