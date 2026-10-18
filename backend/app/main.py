from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.finance import router as finance_router
from app.imports import router as imports_router
from app.models import Account, Employer, Person, Reconciliation, Transaction, User
from app.ownership import validate_financial_associations
from app.schemas import (
    AccountResponse,
    EmployerCreate,
    EmployerResponse,
    LoginRequest,
    PersonCreate,
    PersonResponse,
    RegisterRequest,
    ThemeUpdate,
    TransactionResponse,
    TransactionUpdate,
    UserResponse,
)
from app.security import create_session, current_user, hash_password, verify_password
from app.services import person_balances

app = FastAPI(title="Expense Analyzer API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(imports_router)
app.include_router(finance_router)


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


@app.get("/accounts", response_model=list[AccountResponse])
def accounts(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.execute(
        select(
            Account,
            (Account.opening_balance + func.coalesce(func.sum(Transaction.amount), 0)).label(
                "current_balance"
            ),
            func.count(func.distinct(Transaction.statement_import_id)).label("statement_count"),
        )
        .outerjoin(Transaction, Transaction.account_id == Account.id)
        .where(Account.user_id == user.id)
        .group_by(Account.id)
        .order_by(Account.name)
    ).all()
    return [
        AccountResponse.model_validate(account).model_copy(
            update={"current_balance": balance, "statement_count": statement_count}
        )
        for account, balance, statement_count in rows
    ]


@app.get("/transactions", response_model=list[TransactionResponse])
def transactions(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return db.scalars(
        select(Transaction)
        .where(Transaction.user_id == user.id)
        .order_by(Transaction.txn_date.desc(), Transaction.id.desc())
        .limit(500)
    ).all()


@app.patch("/transactions/{transaction_id}", response_model=TransactionResponse)
def update_transaction(
    transaction_id: int,
    payload: TransactionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    transaction = db.scalar(
        select(Transaction).where(Transaction.id == transaction_id, Transaction.user_id == user.id)
    )
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    changes = payload.model_dump(exclude_unset=True)
    purpose = changes.get("purpose", transaction.purpose)
    associations = validate_financial_associations(
        db,
        user_id=user.id,
        purpose=purpose,
        person_id=changes.get("person_id", transaction.person_id),
        employer_id=changes.get("employer_id", transaction.employer_id),
        trip_id=changes.get("trip_id", transaction.trip_id),
    )
    changes.update(
        person_id=associations.person_id,
        employer_id=associations.employer_id,
        trip_id=associations.trip_id,
    )
    for field, value in changes.items():
        setattr(transaction, field, value)
    for reconciliation in db.scalars(
        select(Reconciliation).where(
            Reconciliation.user_id == user.id,
            Reconciliation.account_id == transaction.account_id,
            Reconciliation.period_start <= transaction.txn_date,
            Reconciliation.period_end >= transaction.txn_date,
        )
    ):
        reconciliation.status = "invalidated"
    db.commit()
    db.refresh(transaction)
    return transaction


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
    db.commit()
    db.refresh(employer)
    return employer
