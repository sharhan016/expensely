import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.counterparty_service import extract_counterparty
from app.database import get_db
from app.models import (
    Account,
    ClassificationRule,
    Counterparty,
    CounterpartyAlias,
    ImportBatch,
    ImportRow,
    ImportStatus,
    Purpose,
    Reconciliation,
    StatementImport,
    Transaction,
    TransactionMeaning,
    User,
)
from app.ownership import validate_financial_associations
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
from app.transfer_matching import match_card_payments

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


def _resolve_counterparty(db: Session, row: ImportRow, user_id: int) -> int | None:
    if row.counterparty_id is not None:
        return row.counterparty_id
    if (
        row.counterparty_confidence not in {"high", "confirmed"}
        or not row.counterparty_alias_type
        or not row.counterparty_alias_value
    ):
        return None
    existing = db.scalar(
        select(CounterpartyAlias)
        .where(
            CounterpartyAlias.user_id == user_id,
            CounterpartyAlias.alias_type == row.counterparty_alias_type,
            CounterpartyAlias.alias_value == row.counterparty_alias_value,
        )
        .order_by(CounterpartyAlias.id)
    )
    if existing:
        return existing.counterparty_id
    counterparty = Counterparty(
        user_id=user_id,
        display_name=row.counterparty_name or row.counterparty_alias_value,
        kind="unknown",
        is_confirmed=False,
    )
    db.add(counterparty)
    db.flush()
    db.add(
        CounterpartyAlias(
            user_id=user_id,
            counterparty_id=counterparty.id,
            alias_type=row.counterparty_alias_type,
            alias_value=row.counterparty_alias_value,
            is_confirmed=False,
        )
    )
    return counterparty.id


def _stage_parsed(db: Session, statement: StatementImport, parsed, user_id: int) -> None:
    statement.bank_slug = parsed.bank_slug
    statement.detected_account_name = parsed.account_name
    statement.account_hint = parsed.source_hint
    statement.statement_kind = parsed.statement_kind
    statement.instrument_confidence = parsed.instrument_confidence
    statement.parser_name = parsed.parser_name
    statement.parser_version = parsed.parser_version
    statement.period_start = parsed.period_start
    statement.period_end = parsed.period_end
    statement.opening_balance = parsed.opening_balance
    statement.closing_balance = parsed.closing_balance
    if parsed.opening_balance is not None and parsed.closing_balance is not None:
        _, difference = calculate_reconciliation(
            parsed.opening_balance,
            [transaction.amount for transaction in parsed.transactions],
            parsed.closing_balance,
        )
        statement.reconciliation_status = "balanced" if difference == 0 else "difference"
        statement.reconciliation_difference = difference
    else:
        statement.reconciliation_status = "unavailable"
        statement.reconciliation_difference = None
    if parsed.source_hint:
        last_four = parsed.source_hint[-4:]
        statement.account_id = db.scalar(
            select(Account.id)
            .where(
                Account.user_id == user_id,
                Account.bank_slug == parsed.bank_slug,
                Account.kind == parsed.statement_kind,
                Account.account_hint.endswith(last_four),
            )
            .order_by(Account.id)
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
    alias_rows = db.execute(
        select(CounterpartyAlias, Counterparty)
        .join(Counterparty, Counterparty.id == CounterpartyAlias.counterparty_id)
        .where(
            CounterpartyAlias.user_id == user_id,
            Counterparty.user_id == user_id,
        )
    ).all()
    aliases = {
        (alias.alias_type, alias.alias_value): (alias, counterparty)
        for alias, counterparty in alias_rows
    }
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
        candidate = extract_counterparty(transaction.description)
        known_alias = (
            aliases.get((candidate.alias_type, candidate.alias_value)) if candidate else None
        )
        known_counterparty = known_alias[1] if known_alias else None
        counterparty_purpose = (
            known_counterparty.default_purpose
            if known_counterparty and known_counterparty.is_confirmed
            else None
        )
        selected_purpose = (
            matched_rule.purpose
            if matched_rule
            else counterparty_purpose or suggested_purpose or Purpose.OTHER
        )
        classification_confidence = (
            "confident"
            if matched_rule or counterparty_purpose is not None
            else suggestion_confidence
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
                purpose=selected_purpose,
                person_id=matched_rule.person_id if matched_rule else None,
                employer_id=matched_rule.employer_id if matched_rule else None,
                counterparty_id=known_counterparty.id if known_counterparty else None,
                counterparty_name=(
                    known_counterparty.display_name
                    if known_counterparty
                    else candidate.display_name
                    if candidate
                    else ""
                ),
                counterparty_alias_type=candidate.alias_type if candidate else "",
                counterparty_alias_value=candidate.alias_value if candidate else "",
                counterparty_confidence=(
                    "confirmed"
                    if known_alias and known_alias[0].is_confirmed
                    else candidate.confidence
                    if candidate
                    else "unresolved"
                ),
                confidence=classification_confidence,
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
    changes = payload.model_dump(exclude_unset=True)
    associations = validate_financial_associations(
        db,
        user_id=user.id,
        purpose=changes.get("purpose", row.purpose),
        person_id=changes.get("person_id", row.person_id),
        employer_id=changes.get("employer_id", row.employer_id),
        trip_id=changes.get("trip_id", row.trip_id),
        counterparty_id=changes.get("counterparty_id", row.counterparty_id),
    )
    changes.update(
        person_id=associations.person_id,
        employer_id=associations.employer_id,
        trip_id=associations.trip_id,
        counterparty_id=associations.counterparty_id,
    )
    for field, value in changes.items():
        setattr(row, field, value)
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
    changes = payload.model_dump(exclude={"row_ids"}, exclude_unset=True)
    validated = []
    for row in selected_rows:
        associations = validate_financial_associations(
            db,
            user_id=user.id,
            purpose=changes.get("purpose", row.purpose),
            person_id=changes.get("person_id", row.person_id),
            employer_id=changes.get("employer_id", row.employer_id),
            trip_id=changes.get("trip_id", row.trip_id),
            counterparty_id=changes.get("counterparty_id", row.counterparty_id),
        )
        validated.append((row, associations))
    for row, associations in validated:
        row_changes = {
            **changes,
            "person_id": associations.person_id,
            "employer_id": associations.employer_id,
            "trip_id": associations.trip_id,
            "counterparty_id": associations.counterparty_id,
        }
        for field, value in row_changes.items():
            setattr(row, field, value)
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
    if (
        statement.reconciliation_status == "difference"
        and not payload.allow_reconciliation_difference
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "This statement has a balance difference. Review it and explicitly confirm the "
                "difference before posting."
            ),
        )
    selected_rows = [row for row in statement.rows if row.selected]
    row_associations = {
        row.id: validate_financial_associations(
            db,
            user_id=user.id,
            purpose=row.purpose,
            person_id=row.person_id,
            employer_id=row.employer_id,
            trip_id=row.trip_id,
            counterparty_id=row.counterparty_id,
        )
        for row in selected_rows
    }
    account = None
    if payload.account_id:
        account = db.scalar(
            select(Account).where(Account.id == payload.account_id, Account.user_id == user.id)
        )
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        if statement.bank_slug != "unknown" and account.bank_slug != statement.bank_slug:
            raise HTTPException(
                status_code=422,
                detail="The selected account belongs to a different bank",
            )
        if statement.instrument_confidence == "high" and account.kind != statement.statement_kind:
            raise HTTPException(
                status_code=422,
                detail="The selected account type does not match this statement",
            )
        if statement.instrument_confidence != "high":
            statement.statement_kind = account.kind
    elif statement.account_id:
        account = db.scalar(
            select(Account).where(Account.id == statement.account_id, Account.user_id == user.id)
        )
    elif statement.account_hint:
        last_four = statement.account_hint[-4:]
        account = db.scalar(
            select(Account)
            .where(
                Account.user_id == user.id,
                Account.bank_slug == statement.bank_slug,
                Account.kind == statement.statement_kind,
                Account.account_hint.endswith(last_four),
            )
            .order_by(Account.id)
        )
    if account is None:
        if payload.statement_kind:
            statement.statement_kind = payload.statement_kind
        bank_slug = statement.bank_slug
        if bank_slug == "unknown":
            bank_slug = payload.bank_slug or ""
            if not bank_slug or bank_slug == "unknown":
                raise HTTPException(
                    status_code=422,
                    detail="Choose the bank before creating this account",
                )
        account_name = (payload.account_name or statement.detected_account_name or "").strip()
        if not account_name:
            raise HTTPException(status_code=422, detail="Enter an account name")
        account = Account(
            user_id=user.id,
            name=account_name,
            bank_slug=bank_slug,
            account_hint=statement.account_hint or uuid.uuid4().hex[:8],
            kind=statement.statement_kind,
            opening_balance=statement.opening_balance or 0,
        )
        db.add(account)
        db.flush()
        statement.bank_slug = bank_slug
    imported = skipped = 0
    for row in statement.rows:
        if not row.selected:
            skipped += int(row.duplicate_of_id is not None)
            continue
        associations = row_associations[row.id]
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
        movement = Transaction(
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
            person_id=associations.person_id,
            employer_id=associations.employer_id,
            trip_id=associations.trip_id,
        )
        counterparty_id = _resolve_counterparty(db, row, user.id)
        movement.meaning = TransactionMeaning(
            user_id=user.id,
            purpose=row.purpose,
            person_id=associations.person_id,
            employer_id=associations.employer_id,
            trip_id=associations.trip_id,
            counterparty_id=counterparty_id,
            version=1,
            review_status="current",
        )
        db.add(movement)
        imported += 1
    statement.account_id = account.id
    statement.status = ImportStatus.CONFIRMED
    db.flush()
    linked_card_payments = len(match_card_payments(db, user.id))
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
        imported=imported,
        skipped_duplicates=skipped,
        account_id=account.id,
        linked_card_payments=linked_card_payments,
    )
