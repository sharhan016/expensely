import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database import get_db
from app.models import (
    Account,
    ClassificationRule,
    Employer,
    ImportBatch,
    ImportRow,
    ImportStatus,
    Purpose,
    Reconciliation,
    StatementImport,
    Transaction,
    User,
)
from app.parsers import ParseError, parse_statement
from app.schemas import (
    ImportBulkUpdate,
    ImportConfirmRequest,
    ImportConfirmResponse,
    ImportRowResponse,
    ImportRowUpdate,
    PdfPasswordRequest,
    StatementImportResponse,
)
from app.security import current_user
from app.services import calculate_reconciliation, dedupe_hash, suggest_from_history

router = APIRouter(prefix="/imports", tags=["statement imports"])
ALLOWED_EXTENSIONS = {".pdf", ".csv", ".txt"}


def _owned_import(db: Session, import_id: uuid.UUID, user_id: int) -> StatementImport:
    statement = db.scalar(
        select(StatementImport)
        .options(selectinload(StatementImport.rows))
        .where(StatementImport.id == import_id, StatementImport.user_id == user_id)
    )
    if not statement:
        raise HTTPException(status_code=404, detail="Statement import not found")
    return statement


def _stage_parsed(db: Session, statement: StatementImport, parsed, user_id: int) -> None:
    statement.bank_slug = parsed.bank_slug
    statement.account_hint = parsed.source_hint
    statement.statement_kind = parsed.statement_kind
    statement.period_start = parsed.period_start
    statement.period_end = parsed.period_end
    statement.opening_balance = parsed.opening_balance
    statement.closing_balance = parsed.closing_balance
    if parsed.source_hint:
        statement.account_id = db.scalar(
            select(Account.id).where(
                Account.user_id == user_id,
                Account.bank_slug == parsed.bank_slug,
                Account.account_hint == parsed.source_hint,
            )
        )
    if parsed.period_start and parsed.period_end and (statement.account_id or parsed.source_hint):
        identity = (
            StatementImport.account_id == statement.account_id
            if statement.account_id
            else (
                (StatementImport.bank_slug == parsed.bank_slug)
                & (StatementImport.account_hint == parsed.source_hint)
            )
        )
        statement.overlap_warning = (
            db.scalar(
                select(StatementImport.id).where(
                    StatementImport.user_id == user_id,
                    StatementImport.id != statement.id,
                    StatementImport.period_start <= parsed.period_end,
                    StatementImport.period_end >= parsed.period_start,
                    identity,
                )
            )
            is not None
        )
    rules = db.scalars(
        select(ClassificationRule).where(
            ClassificationRule.user_id == user_id,
            ClassificationRule.is_active.is_(True),
        )
    ).all()
    history = list(
        db.execute(
            select(Transaction.description, Transaction.purpose).where(
                Transaction.user_id == user_id, Transaction.purpose != Purpose.OTHER
            )
        ).all()
    )
    statement.rows.clear()
    for transaction in parsed.transactions:
        duplicate = db.scalar(
            select(Transaction.id).where(
                Transaction.user_id == user_id,
                Transaction.txn_date == transaction.txn_date,
                Transaction.amount == transaction.amount,
                Transaction.description == transaction.description,
            )
        )
        matched_rule = next(
            (
                rule
                for rule in rules
                if rule.description_contains.casefold() in transaction.description.casefold()
                and (rule.min_amount is None or abs(transaction.amount) >= rule.min_amount)
                and (rule.max_amount is None or abs(transaction.amount) <= rule.max_amount)
            ),
            None,
        )
        suggested_purpose, suggestion_confidence = suggest_from_history(
            transaction.description, history
        )
        statement.rows.append(
            ImportRow(
                txn_date=transaction.txn_date,
                description=transaction.description,
                amount=transaction.amount,
                balance=transaction.balance,
                reference=transaction.reference,
                duplicate_of_id=duplicate,
                selected=duplicate is None,
                purpose=(
                    matched_rule.purpose if matched_rule else suggested_purpose or Purpose.OTHER
                ),
                person_id=matched_rule.person_id if matched_rule else None,
                employer_id=matched_rule.employer_id if matched_rule else None,
                confidence="confident" if matched_rule else suggestion_confidence,
            )
        )
    statement.status = ImportStatus.READY
    statement.error_message = ""


@router.post("", response_model=list[StatementImportResponse], status_code=201)
def upload_statements(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if not files or len(files) > 20:
        raise HTTPException(status_code=422, detail="Upload between 1 and 20 statements")
    batch = ImportBatch(user_id=user.id)
    db.add(batch)
    db.flush()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for upload in files:
        filename = Path(upload.filename or "statement").name
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=415, detail=f"{filename}: unsupported file type")
        content = upload.file.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail=f"{filename}: file is too large")
        if suffix == ".pdf" and not content.startswith(b"%PDF-"):
            raise HTTPException(status_code=415, detail=f"{filename}: not a valid PDF file")
        if suffix in {".csv", ".txt"} and b"\x00" in content[:4096]:
            raise HTTPException(status_code=415, detail=f"{filename}: not a text statement")
        storage_key = f"{user.id}/{uuid.uuid4().hex}{suffix}"
        destination = settings.upload_dir / storage_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        statement = StatementImport(
            batch_id=batch.id,
            user_id=user.id,
            original_filename=filename,
            storage_key=storage_key,
            status=ImportStatus.PROCESSING,
        )
        db.add(statement)
        db.flush()
        try:
            parsed = parse_statement(destination)
            _stage_parsed(db, statement, parsed, user.id)
        except ParseError as exc:
            statement.status = (
                ImportStatus.PASSWORD_REQUIRED
                if "password" in str(exc).lower()
                else ImportStatus.FAILED
            )
            statement.error_message = str(exc)
        results.append(statement)
    db.commit()
    return [_owned_import(db, item.id, user.id) for item in results]


@router.post("/{import_id}/unlock", response_model=StatementImportResponse)
def unlock_pdf(
    import_id: uuid.UUID,
    payload: PdfPasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    statement = _owned_import(db, import_id, user.id)
    if statement.status != ImportStatus.PASSWORD_REQUIRED:
        raise HTTPException(status_code=409, detail="This statement is not waiting for a password")
    try:
        parsed = parse_statement(
            settings.upload_dir / statement.storage_key,
            password=payload.password.get_secret_value(),
        )
        _stage_parsed(db, statement, parsed, user.id)
        db.commit()
    except ParseError as exc:
        raise HTTPException(status_code=422, detail="The password did not unlock this PDF") from exc
    return _owned_import(db, statement.id, user.id)


@router.get("", response_model=list[StatementImportResponse])
def list_imports(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return db.scalars(
        select(StatementImport)
        .options(selectinload(StatementImport.rows))
        .where(StatementImport.user_id == user.id)
        .order_by(StatementImport.created_at.desc())
        .limit(100)
    ).all()


@router.get("/{import_id}", response_model=StatementImportResponse)
def get_import(
    import_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    return _owned_import(db, import_id, user.id)


@router.patch("/{import_id}/rows/{row_id}", response_model=ImportRowResponse)
def update_import_row(
    import_id: uuid.UUID,
    row_id: int,
    payload: ImportRowUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    statement = _owned_import(db, import_id, user.id)
    row = next((item for item in statement.rows if item.id == row_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="Import row not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    if row.purpose == Purpose.BUSINESS and row.employer_id is None:
        row.employer_id = db.scalar(
            select(Employer.id).where(Employer.user_id == user.id, Employer.is_default.is_(True))
        )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{import_id}/rows", response_model=list[ImportRowResponse])
def bulk_update_import_rows(
    import_id: uuid.UUID,
    payload: ImportBulkUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    statement = _owned_import(db, import_id, user.id)
    selected_rows = [row for row in statement.rows if row.id in set(payload.row_ids)]
    if len(selected_rows) != len(set(payload.row_ids)):
        raise HTTPException(status_code=404, detail="One or more import rows were not found")
    for row in selected_rows:
        for field, value in payload.model_dump(exclude={"row_ids"}, exclude_unset=True).items():
            setattr(row, field, value)
        if row.purpose == Purpose.BUSINESS and row.employer_id is None:
            row.employer_id = db.scalar(
                select(Employer.id).where(
                    Employer.user_id == user.id, Employer.is_default.is_(True)
                )
            )
    db.commit()
    return selected_rows


@router.post("/{import_id}/confirm", response_model=ImportConfirmResponse)
def confirm_import(
    import_id: uuid.UUID,
    payload: ImportConfirmRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    statement = _owned_import(db, import_id, user.id)
    if statement.status != ImportStatus.READY:
        raise HTTPException(status_code=409, detail="Only reviewed statements can be confirmed")
    account = None
    if payload.account_id:
        account = db.scalar(
            select(Account).where(Account.id == payload.account_id, Account.user_id == user.id)
        )
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
    elif statement.account_id:
        account = db.scalar(
            select(Account).where(Account.id == statement.account_id, Account.user_id == user.id)
        )
    elif statement.account_hint:
        account = db.scalar(
            select(Account).where(
                Account.user_id == user.id,
                Account.bank_slug == statement.bank_slug,
                Account.account_hint == statement.account_hint,
            )
        )
    if account is None:
        account = Account(
            user_id=user.id,
            name=payload.account_name or f"{statement.bank_slug.title()} account",
            bank_slug=statement.bank_slug,
            account_hint=statement.account_hint or uuid.uuid4().hex[:8],
            kind=statement.statement_kind,
            opening_balance=statement.opening_balance or 0,
        )
        db.add(account)
        db.flush()
    imported = skipped = 0
    for row in statement.rows:
        if not row.selected:
            skipped += int(row.duplicate_of_id is not None)
            continue
        fingerprint = dedupe_hash(
            account.id, row.txn_date, row.amount, row.description, row.reference
        )
        if db.scalar(
            select(Transaction.id).where(
                Transaction.user_id == user.id, Transaction.dedupe_hash == fingerprint
            )
        ):
            skipped += 1
            continue
        db.add(
            Transaction(
                user_id=user.id,
                account_id=account.id,
                statement_import_id=statement.id,
                txn_date=row.txn_date,
                description=row.description,
                amount=row.amount,
                bank_balance=row.balance,
                reference=row.reference,
                dedupe_hash=fingerprint,
                purpose=row.purpose,
                person_id=row.person_id,
                employer_id=row.employer_id,
                trip_id=row.trip_id,
            )
        )
        imported += 1
    statement.account_id = account.id
    statement.status = ImportStatus.CONFIRMED
    db.flush()
    if (
        statement.period_start
        and statement.period_end
        and statement.opening_balance is not None
        and statement.closing_balance is not None
    ):
        movements = list(
            db.scalars(
                select(Transaction.amount).where(
                    Transaction.user_id == user.id,
                    Transaction.account_id == account.id,
                    Transaction.txn_date.between(statement.period_start, statement.period_end),
                )
            )
        )
        _, difference = calculate_reconciliation(
            statement.opening_balance, movements, statement.closing_balance
        )
        reconciliation = db.scalar(
            select(Reconciliation).where(
                Reconciliation.user_id == user.id,
                Reconciliation.account_id == account.id,
                Reconciliation.period_start == statement.period_start,
                Reconciliation.period_end == statement.period_end,
            )
        )
        if not reconciliation:
            reconciliation = Reconciliation(
                user_id=user.id,
                account_id=account.id,
                period_start=statement.period_start,
                period_end=statement.period_end,
                opening_balance=statement.opening_balance,
                statement_closing_balance=statement.closing_balance,
            )
            db.add(reconciliation)
        reconciliation.status = "balanced" if difference == 0 else "difference"
    db.commit()
    return ImportConfirmResponse(
        imported=imported, skipped_duplicates=skipped, account_id=account.id
    )
