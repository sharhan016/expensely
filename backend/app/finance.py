from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Account,
    ClassificationRule,
    Employer,
    ImportRow,
    Person,
    Purpose,
    Reconciliation,
    StatementImport,
    Transaction,
    Trip,
    TripTransactionExclusion,
    User,
)
from app.schemas import (
    ReconciliationCreate,
    RuleCreate,
    RuleUpdate,
    TripCreate,
    TripTransactionDecision,
)
from app.security import current_user
from app.services import calculate_reconciliation, person_balances

router = APIRouter(tags=["financial workspace"])


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(current_user)):
    transactions = db.scalars(select(Transaction).where(Transaction.user_id == user.id)).all()
    accounts = db.scalars(select(Account).where(Account.user_id == user.id)).all()
    bank_accounts = [account for account in accounts if account.kind != "credit_card"]
    credit_accounts = [account for account in accounts if account.kind == "credit_card"]
    bank_account_ids = {account.id for account in bank_accounts}
    credit_account_ids = {account.id for account in credit_accounts}
    bank = sum((account.opening_balance for account in bank_accounts), Decimal("0")) + sum(
        (
            transaction.amount
            for transaction in transactions
            if transaction.account_id in bank_account_ids
        ),
        Decimal("0"),
    )
    credit_position = sum(
        (account.opening_balance for account in credit_accounts), Decimal("0")
    ) + sum(
        (
            transaction.amount
            for transaction in transactions
            if transaction.account_id in credit_account_ids
        ),
        Decimal("0"),
    )
    credit_card_balance = max(-credit_position, Decimal("0"))
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
        "credit_card_balance": credit_card_balance,
        "entrusted_balance": entrusted,
        "available_balance": bank - entrusted,
        "company_receivable": max(spent - reimbursed, Decimal("0")),
        "loan_balance": loans,
        "unresolved_count": sum(t.purpose == Purpose.OTHER for t in transactions),
        "account_count": len(bank_accounts),
        "credit_card_count": len(credit_accounts),
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
    if not payload.starts_on or not payload.ends_on:
        raise HTTPException(status_code=422, detail="Travel start and end dates are required")
    if payload.starts_on > payload.ends_on:
        raise HTTPException(status_code=422, detail="Travel end must be after the start")
    window_start = payload.purchase_window_start or payload.starts_on
    window_end = payload.purchase_window_end or payload.ends_on
    if window_start > payload.starts_on or window_end < payload.ends_on:
        raise HTTPException(
            status_code=422, detail="The purchase window must include the travel dates"
        )
    trip = Trip(
        user_id=user.id,
        name=payload.name,
        starts_on=payload.starts_on,
        ends_on=payload.ends_on,
        purchase_window_start=window_start,
        purchase_window_end=window_end,
    )
    db.add(trip)
    db.commit()
    db.refresh(trip)
    return trip


@router.get("/trips/{trip_id}")
def trip_detail(trip_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    trip = db.scalar(select(Trip).where(Trip.id == trip_id, Trip.user_id == user.id))
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    window_start = trip.purchase_window_start or trip.starts_on
    window_end = trip.purchase_window_end or trip.ends_on
    transaction_query = (
        select(Transaction, Account.name)
        .join(Account, Account.id == Transaction.account_id)
        .where(Transaction.user_id == user.id)
        .order_by(Transaction.txn_date, Transaction.id)
    )
    if window_start:
        transaction_query = transaction_query.where(Transaction.txn_date >= window_start)
    if window_end:
        transaction_query = transaction_query.where(Transaction.txn_date <= window_end)
    rows = db.execute(transaction_query).all()
    excluded_ids = set(
        db.scalars(
            select(TripTransactionExclusion.transaction_id).where(
                TripTransactionExclusion.trip_id == trip.id
            )
        ).all()
    )
    candidates = []
    for transaction, account_name in rows:
        status = (
            "included"
            if transaction.trip_id == trip.id
            else "excluded"
            if transaction.id in excluded_ids
            else "unavailable"
            if transaction.trip_id is not None
            else "pending"
        )
        candidates.append(
            {"transaction": transaction, "account_name": account_name, "status": status}
        )
    return {"trip": trip, "candidates": candidates}


@router.patch("/trips/{trip_id}/transactions/{transaction_id}")
def decide_trip_transaction(
    trip_id: int,
    transaction_id: int,
    payload: TripTransactionDecision,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    trip = db.scalar(select(Trip).where(Trip.id == trip_id, Trip.user_id == user.id))
    transaction = db.scalar(
        select(Transaction).where(Transaction.id == transaction_id, Transaction.user_id == user.id)
    )
    if not trip or not transaction:
        raise HTTPException(status_code=404, detail="Trip or transaction not found")
    if transaction.trip_id not in (None, trip.id):
        raise HTTPException(status_code=409, detail="Transaction belongs to another trip")
    exclusion = db.scalar(
        select(TripTransactionExclusion).where(
            TripTransactionExclusion.trip_id == trip.id,
            TripTransactionExclusion.transaction_id == transaction.id,
        )
    )
    if payload.status == "included":
        transaction.trip_id = trip.id
        if exclusion:
            db.delete(exclusion)
    elif payload.status == "excluded":
        if transaction.trip_id == trip.id:
            transaction.trip_id = None
        if not exclusion:
            db.add(TripTransactionExclusion(trip_id=trip.id, transaction_id=transaction.id))
    else:
        if transaction.trip_id == trip.id:
            transaction.trip_id = None
        if exclusion:
            db.delete(exclusion)
    db.commit()
    return {"status": payload.status}


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


@router.patch("/rules/{rule_id}")
def update_rule(
    rule_id: int,
    payload: RuleUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    rule = db.scalar(
        select(ClassificationRule).where(
            ClassificationRule.id == rule_id, ClassificationRule.user_id == user.id
        )
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    rule = db.scalar(
        select(ClassificationRule).where(
            ClassificationRule.id == rule_id, ClassificationRule.user_id == user.id
        )
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(rule)
    db.commit()


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
    unresolved_count = db.scalar(
        select(func.count(Transaction.id)).where(
            Transaction.user_id == user.id,
            Transaction.account_id == account.id,
            Transaction.txn_date.between(payload.period_start, payload.period_end),
            Transaction.purpose == Purpose.OTHER,
        )
    )
    possible_duplicate_count = db.scalar(
        select(func.count(ImportRow.id))
        .join(StatementImport, StatementImport.id == ImportRow.statement_import_id)
        .where(
            StatementImport.user_id == user.id,
            StatementImport.account_id == account.id,
            ImportRow.txn_date.between(payload.period_start, payload.period_end),
            ImportRow.duplicate_of_id.is_not(None),
        )
    )
    db.commit()
    db.refresh(reconciliation)
    return {
        "reconciliation": reconciliation,
        "expected_closing_balance": expected,
        "difference": difference,
        "movement_count": len(movements),
        "unresolved_count": unresolved_count,
        "possible_duplicate_count": possible_duplicate_count,
        "cause": None,
    }
