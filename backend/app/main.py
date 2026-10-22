import logging
from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import coverage_hooks  # noqa: F401  (registers reconciliation invalidation)
from app.action_routes import router as action_router
from app.assistant_routes import router as assistant_router
from app.balances import account_balances, ledger_running_balances
from app.cash import neutral_movement_ids
from app.cash_routes import router as cash_router
from app.config import settings
from app.counterparty_routes import router as counterparty_router
from app.coverage_routes import router as coverage_router
from app.database import get_db
from app.finance import router as finance_router
from app.group_routes import router as group_router
from app.imports import router as imports_router
from app.investigate.routes import router as investigate_router
from app.meanings import apply_meaning_change, ensure_meaning
from app.models import (
    Account,
    Employer,
    Person,
    StatementImport,
    Transaction,
    TransactionMeaning,
    TransactionMeaningChange,
    User,
)
from app.ownership import validate_financial_associations
from app.report_routes import router as report_router
from app.schemas import (
    AccountResponse,
    AccountUpdate,
    EmployerCreate,
    EmployerResponse,
    LoginRequest,
    PersonCreate,
    PersonResponse,
    RegisterRequest,
    ThemeUpdate,
    TransactionMeaningChangeResponse,
    TransactionPage,
    TransactionResponse,
    TransactionUpdate,
    UserResponse,
)
from app.security import create_session, current_user, hash_password, verify_password
from app.services import person_balances
from app.transfer_routes import router as transfer_router
from app.workflow.routes import router as workflow_router

app = FastAPI(title="Expense Analyzer API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger = logging.getLogger("expense.api")


@app.middleware("http")
async def contain_unexpected_errors(request: Request, call_next):
    """Log only the error class and route for unhandled failures.

    The default server traceback prints exception messages and SQL parameters, which can hold
    transaction descriptions, names, amounts and account numbers.
    """
    try:
        return await call_next(request)
    except Exception as exc:
        route = request.scope.get("route")
        logger.error(
            "Unhandled %s on %s %s",
            type(exc).__name__,
            request.method,
            getattr(route, "path", "unmatched"),
        )
        return JSONResponse(
            status_code=500, content={"detail": "Something went wrong. Nothing was changed."}
        )


app.include_router(imports_router)
app.include_router(finance_router)
app.include_router(counterparty_router)
app.include_router(group_router)
app.include_router(transfer_router)
app.include_router(assistant_router)
app.include_router(coverage_router)
app.include_router(cash_router)
app.include_router(workflow_router)
app.include_router(report_router)
app.include_router(investigate_router)
app.include_router(action_router)


def _transaction_response(transaction: Transaction) -> TransactionResponse:
    response = TransactionResponse.model_validate(transaction)
    if not transaction.meaning:
        return response
    return response.model_copy(
        update={
            "purpose": transaction.meaning.purpose,
            "person_id": transaction.meaning.person_id,
            "employer_id": transaction.meaning.employer_id,
            "trip_id": transaction.meaning.trip_id,
            "counterparty_id": transaction.meaning.counterparty_id,
            "counterparty_name": (
                transaction.meaning.counterparty.display_name
                if transaction.meaning.counterparty
                else ""
            ),
            "category": transaction.meaning.category,
            "category_confidence": transaction.meaning.category_confidence,
            "classification_evidence": transaction.meaning.classification_evidence,
            "decision_source": transaction.meaning.decision_source,
            "notes": transaction.meaning.notes,
            "meaning_version": transaction.meaning.version,
        }
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/auth/register", response_model=UserResponse, status_code=201)
def register(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == payload.email.lower())):
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    user = User(
        email=payload.email.lower(),
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    response.set_cookie("expense_session", create_session(user.id), httponly=True, samesite="lax")
    return user


@app.post("/auth/login", response_model=UserResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    response.set_cookie("expense_session", create_session(user.id), httponly=True, samesite="lax")
    return user


@app.post("/auth/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie("expense_session")


@app.get("/auth/me", response_model=UserResponse)
def me(user: User = Depends(current_user)):
    return user


@app.patch("/settings/theme", response_model=UserResponse)
def update_theme(
    payload: ThemeUpdate, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    allowed = {"cobalt", "emerald", "amber", "coral", "indigo", "slate"}
    if payload.accent not in allowed:
        raise HTTPException(status_code=422, detail="Unsupported accent")
    user.accent = payload.accent
    db.commit()
    return user


def _account_response(account: Account, db: Session, user_id: int) -> AccountResponse:
    balance = next(item for item in account_balances(db, user_id) if item.account_id == account.id)
    statement_count = db.scalar(
        select(func.count(func.distinct(Transaction.statement_import_id))).where(
            Transaction.account_id == account.id
        )
    )
    return AccountResponse.model_validate(account).model_copy(
        update={
            "current_balance": balance.balance,
            "statement_count": statement_count or 0,
            "as_of": balance.as_of,
            "statement_end": balance.statement_end,
            "age_days": balance.age_days,
            "is_stale": balance.is_stale,
        }
    )


@app.get("/accounts", response_model=list[AccountResponse])
def accounts(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Account).where(Account.user_id == user.id).order_by(Account.name))
    return [_account_response(account, db, user.id) for account in rows]


@app.patch("/accounts/{account_id}", response_model=AccountResponse)
def update_account(
    account_id: int,
    payload: AccountUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    account = db.scalar(select(Account).where(Account.id == account_id, Account.user_id == user.id))
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    account.name = payload.name.strip()
    account.bank_slug = payload.bank_slug
    for statement in db.scalars(
        select(StatementImport).where(
            StatementImport.account_id == account.id,
            StatementImport.user_id == user.id,
            StatementImport.bank_slug == "unknown",
        )
    ):
        statement.bank_slug = payload.bank_slug
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="An account with this bank and identity already exists"
        ) from exc
    return _account_response(account, db, user.id)


@app.get("/transactions", response_model=TransactionPage)
def transactions(
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    filters = [Transaction.user_id == user.id]
    if date_from:
        filters.append(Transaction.txn_date >= date_from)
    if date_to:
        filters.append(Transaction.txn_date <= date_to)
    if account_id is not None:
        filters.append(Transaction.account_id == account_id)
    total = db.scalar(select(func.count(Transaction.id)).where(*filters)) or 0
    rows = db.scalars(
        select(Transaction)
        .options(selectinload(Transaction.meaning).selectinload(TransactionMeaning.counterparty))
        .where(*filters)
        .order_by(Transaction.txn_date.desc(), Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    balances = ledger_running_balances(db, user.id, {row.id for row in rows})
    cash_ids = neutral_movement_ids(db, user.id)
    items = [
        _transaction_response(transaction).model_copy(
            update={
                "running_balance": balances.get(transaction.id),
                "is_cash_withdrawal": transaction.id in cash_ids,
            }
        )
        for transaction in rows
    ]
    return TransactionPage(items=items, total=total, showing=len(items), limit=limit, offset=offset)


@app.patch("/transactions/{transaction_id}", response_model=TransactionResponse)
def update_transaction(
    transaction_id: int,
    payload: TransactionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    transaction = db.scalar(
        select(Transaction)
        .options(selectinload(Transaction.meaning).selectinload(TransactionMeaning.counterparty))
        .where(Transaction.id == transaction_id, Transaction.user_id == user.id)
    )
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    changes = payload.model_dump(exclude={"expected_version", "reason"}, exclude_unset=True)
    current = transaction.meaning
    purpose = changes.get("purpose", current.purpose if current else transaction.purpose)
    associations = validate_financial_associations(
        db,
        user_id=user.id,
        purpose=purpose,
        person_id=changes.get("person_id", current.person_id if current else transaction.person_id),
        employer_id=changes.get(
            "employer_id", current.employer_id if current else transaction.employer_id
        ),
        trip_id=changes.get("trip_id", current.trip_id if current else transaction.trip_id),
        counterparty_id=changes.get(
            "counterparty_id", current.counterparty_id if current else None
        ),
    )
    meaning = ensure_meaning(db, transaction)
    apply_meaning_change(
        db,
        transaction=transaction,
        actor_user_id=user.id,
        purpose=purpose,
        person_id=associations.person_id,
        employer_id=associations.employer_id,
        trip_id=associations.trip_id,
        counterparty_id=associations.counterparty_id,
        category=changes.get("category", meaning.category),
        notes=changes.get("notes", meaning.notes),
        expected_version=payload.expected_version,
        reason=payload.reason,
    )
    db.commit()
    db.refresh(transaction)
    return _transaction_response(transaction)


@app.get(
    "/transactions/{transaction_id}/history",
    response_model=list[TransactionMeaningChangeResponse],
)
def transaction_history(
    transaction_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    transaction = db.scalar(
        select(Transaction.id).where(
            Transaction.id == transaction_id, Transaction.user_id == user.id
        )
    )
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return db.scalars(
        select(TransactionMeaningChange)
        .where(
            TransactionMeaningChange.transaction_id == transaction_id,
            TransactionMeaningChange.user_id == user.id,
        )
        .order_by(TransactionMeaningChange.version.desc())
    ).all()


@app.get("/people", response_model=list[PersonResponse])
def people(db: Session = Depends(get_db), user: User = Depends(current_user)):
    result = []
    for person in db.scalars(select(Person).where(Person.user_id == user.id).order_by(Person.name)):
        entrusted, loans = person_balances(db, person)
        result.append(
            PersonResponse.model_validate(person).model_copy(
                update={"entrusted_balance": entrusted, "loan_balance": loans}
            )
        )
    return result


@app.post("/people", response_model=PersonResponse, status_code=201)
def create_person(
    payload: PersonCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    person = Person(user_id=user.id, **payload.model_dump())
    db.add(person)
    db.commit()
    db.refresh(person)
    return PersonResponse.model_validate(person)


@app.get("/employers", response_model=list[EmployerResponse])
def employers(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return db.scalars(
        select(Employer).where(Employer.user_id == user.id).order_by(Employer.name)
    ).all()


@app.post("/employers", response_model=EmployerResponse, status_code=201)
def create_employer(
    payload: EmployerCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    if payload.is_default:
        for employer in db.scalars(select(Employer).where(Employer.user_id == user.id)):
            employer.is_default = False
    employer = Employer(user_id=user.id, **payload.model_dump())
    db.add(employer)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "ux_employers_one_default_per_user" in str(exc.orig):
            raise HTTPException(
                status_code=409,
                detail="Another default employer was saved at the same time; please retry",
            ) from exc
        raise
    db.refresh(employer)
    return employer
