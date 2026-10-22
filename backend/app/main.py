from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.assistant_routes import router as assistant_router
from app.config import settings
from app.counterparty_routes import router as counterparty_router
from app.database import get_db
from app.finance import router as finance_router
from app.imports import router as imports_router
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
    TransactionResponse,
    TransactionUpdate,
    UserResponse,
)
from app.security import create_session, current_user, hash_password, verify_password
from app.services import person_balances
from app.transfer_routes import router as transfer_router

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
app.include_router(counterparty_router)
app.include_router(transfer_router)
app.include_router(assistant_router)


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
    balance, statement_count = db.execute(
        select(
            (Account.opening_balance + func.coalesce(func.sum(Transaction.amount), 0)),
            func.count(func.distinct(Transaction.statement_import_id)),
        )
        .outerjoin(Transaction, Transaction.account_id == Account.id)
        .where(Account.id == account.id)
        .group_by(Account.id)
    ).one()
    return AccountResponse.model_validate(account).model_copy(
        update={"current_balance": balance, "statement_count": statement_count}
    )


@app.get("/transactions", response_model=list[TransactionResponse])
def transactions(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(
        select(Transaction)
        .options(selectinload(Transaction.meaning).selectinload(TransactionMeaning.counterparty))
        .where(Transaction.user_id == user.id)
        .order_by(Transaction.txn_date.desc(), Transaction.id.desc())
        .limit(500)
    ).all()
    return [_transaction_response(transaction) for transaction in rows]


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
