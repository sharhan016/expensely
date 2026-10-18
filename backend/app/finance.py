from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Account,
    ClassificationRule,
    Employer,
    Person,
    Purpose,
    Reconciliation,
    Transaction,
    Trip,
    User,
)
from app.schemas import ReconciliationCreate, RuleCreate, TripCreate
from app.security import current_user
from app.services import calculate_reconciliation, person_balances

router = APIRouter(tags=["financial workspace"])


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(current_user)):
    transactions = db.scalars(select(Transaction).where(Transaction.user_id == user.id)).all()
    opening = sum(
        (a.opening_balance for a in db.scalars(select(Account).where(Account.user_id == user.id))),
        Decimal("0"),
    )
    bank = opening + sum((t.amount for t in transactions), Decimal("0"))
    entrusted = sum(
        (t.amount for t in transactions if t.purpose == Purpose.ENTRUSTED), Decimal("0")
    )
    spent = -sum((t.amount for t in transactions if t.purpose == Purpose.BUSINESS), Decimal("0"))
    reimbursed = sum(
        (t.amount for t in transactions if t.purpose == Purpose.REIMBURSEMENT), Decimal("0")
    )
    loans = sum((-t.amount for t in transactions if t.purpose == Purpose.LOAN_GIVEN), Decimal("0"))
    loans -= sum(
        (t.amount for t in transactions if t.purpose == Purpose.LOAN_REPAYMENT), Decimal("0")
    )
    return {
        "bank_balance": bank,
        "entrusted_balance": entrusted,
        "available_balance": bank - entrusted,
        "company_receivable": max(spent - reimbursed, Decimal("0")),
        "loan_balance": loans,
        "unresolved_count": sum(t.purpose == Purpose.OTHER for t in transactions),
        "account_count": db.scalar(
            select(func.count(Account.id)).where(Account.user_id == user.id)
        ),
    }


@router.get("/people/{person_id}")
def person_detail(
    person_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    person = db.scalar(select(Person).where(Person.id == person_id, Person.user_id == user.id))
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    entrusted, loans = person_balances(db, person)
    transactions = db.scalars(
        select(Transaction)
        .where(Transaction.user_id == user.id, Transaction.person_id == person.id)
        .order_by(Transaction.txn_date.desc())
    ).all()
    return {
        "person": person,
        "entrusted_balance": entrusted,
        "loan_balance": loans,
        "transactions": transactions,
    }


@router.get("/reimbursements")
def reimbursements(db: Session = Depends(get_db), user: User = Depends(current_user)):
    result = []
    for employer in db.scalars(select(Employer).where(Employer.user_id == user.id)):
        expenses = db.scalars(
            select(Transaction).where(
                Transaction.user_id == user.id,
                Transaction.employer_id == employer.id,
                Transaction.purpose == Purpose.BUSINESS,
            )
        ).all()
        received = db.scalar(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.user_id == user.id,
                Transaction.employer_id == employer.id,
                Transaction.purpose == Purpose.REIMBURSEMENT,
            )
        )
        spent = -sum((item.amount for item in expenses), Decimal("0"))
        result.append(
            {
                "employer": employer,
                "expenses": expenses,
                "received": received,
                "outstanding": max(spent - received, Decimal("0")),
            }
        )
    return result


@router.get("/trips")
def trips(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.execute(
        select(
            Trip,
            func.coalesce(
                func.sum(case((Transaction.amount < 0, -Transaction.amount), else_=0)), 0
            ),
            func.count(Transaction.id),
        )
        .outerjoin(Transaction, Transaction.trip_id == Trip.id)
        .where(Trip.user_id == user.id)
        .group_by(Trip.id)
        .order_by(Trip.starts_on.desc().nullslast())
    ).all()
    return [
        {"trip": trip, "total": total, "transaction_count": count} for trip, total, count in rows
    ]


@router.post("/trips", status_code=201)
def create_trip(
    payload: TripCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    trip = Trip(user_id=user.id, **payload.model_dump())
    db.add(trip)
    db.commit()
    db.refresh(trip)
    return trip


@router.get("/rules")
def rules(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return db.scalars(
        select(ClassificationRule)
        .where(ClassificationRule.user_id == user.id)
        .order_by(ClassificationRule.created_at.desc())
    ).all()


@router.post("/rules", status_code=201)
def create_rule(
    payload: RuleCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    rule = ClassificationRule(user_id=user.id, **payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/reconciliations")
def reconciliations(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return db.scalars(
        select(Reconciliation)
        .where(Reconciliation.user_id == user.id)
        .order_by(Reconciliation.period_end.desc())
    ).all()


@router.post("/reconciliations", status_code=201)
def reconcile(
    payload: ReconciliationCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    account = db.scalar(
        select(Account).where(Account.id == payload.account_id, Account.user_id == user.id)
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    movements = list(
        db.scalars(
            select(Transaction.amount).where(
                Transaction.user_id == user.id,
                Transaction.account_id == account.id,
                Transaction.txn_date.between(payload.period_start, payload.period_end),
            )
        )
    )
    expected, difference = calculate_reconciliation(
        payload.opening_balance, movements, payload.statement_closing_balance
    )
    reconciliation = db.scalar(
        select(Reconciliation).where(
            Reconciliation.user_id == user.id,
            Reconciliation.account_id == account.id,
            Reconciliation.period_start == payload.period_start,
            Reconciliation.period_end == payload.period_end,
        )
    )
    if not reconciliation:
        reconciliation = Reconciliation(user_id=user.id, **payload.model_dump())
        db.add(reconciliation)
    else:
        reconciliation.opening_balance = payload.opening_balance
        reconciliation.statement_closing_balance = payload.statement_closing_balance
    reconciliation.status = "balanced" if difference == 0 else "difference"
    db.commit()
    db.refresh(reconciliation)
    return {
        "reconciliation": reconciliation,
        "expected_closing_balance": expected,
        "difference": difference,
        "movement_count": len(movements),
    }
