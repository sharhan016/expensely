from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employer, Person, Purpose, Trip


@dataclass(frozen=True)
class FinancialAssociations:
    person_id: int | None
    employer_id: int | None
    trip_id: int | None


def _require_owned(db: Session, model, entity_id: int | None, user_id: int, label: str) -> None:
    if entity_id is None:
        return
    if db.scalar(select(model.id).where(model.id == entity_id, model.user_id == user_id)) is None:
        # Missing and foreign records intentionally have the same response.
        raise HTTPException(status_code=404, detail=f"{label} not found")


def validate_financial_associations(
    db: Session,
    *,
    user_id: int,
    purpose: Purpose | None,
    person_id: int | None,
    employer_id: int | None,
    trip_id: int | None = None,
) -> FinancialAssociations:
    if purpose is None:
        raise HTTPException(status_code=422, detail="Purpose is required")

    _require_owned(db, Person, person_id, user_id, "Person")
    _require_owned(db, Employer, employer_id, user_id, "Employer")
    _require_owned(db, Trip, trip_id, user_id, "Trip")

    if purpose in {Purpose.BUSINESS, Purpose.REIMBURSEMENT} and employer_id is None:
        employer_id = db.scalar(
            select(Employer.id)
            .where(Employer.user_id == user_id, Employer.is_default.is_(True))
            .order_by(Employer.id)
        )
        if employer_id is None:
            raise HTTPException(
                status_code=422,
                detail="An employer is required for business expenses and reimbursements",
            )

    if (
        purpose in {Purpose.ENTRUSTED, Purpose.LOAN_GIVEN, Purpose.LOAN_REPAYMENT}
        and person_id is None
    ):
        raise HTTPException(
            status_code=422,
            detail="A person is required for entrusted funds, loans, and repayments",
        )

    return FinancialAssociations(
        person_id=person_id,
        employer_id=employer_id,
        trip_id=trip_id,
    )
