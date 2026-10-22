import uuid
from collections import Counter
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.cash import sync_cash_markers
from app.config import settings
from app.counterparty_service import extract_counterparty
from app.coverage_service import record_reconciliation
from app.database import get_db
from app.grouping import sync_transaction_group
from app.models import (
    Account,
    ClassificationRule,
    Counterparty,
    CounterpartyAlias,
    ImportBatch,
    ImportRow,
    ImportStatus,
    InstrumentType,
    Purpose,
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
    PossiblyMissedRow,
    StatementImportResponse,
)
from app.security import current_user
from app.services import (
    assign_ordinals,
    calculate_reconciliation,
    confirmed_history,
    dedupe_hash,
    movement_key,
    suggest_from_history,
)
from app.transfer_matching import match_card_payments, match_card_refunds

router = APIRouter(prefix="/imports", tags=["statement imports"])
ALLOWED_EXTENSIONS = {".pdf", ".csv", ".txt"}


def _mark_purpose_confirmed(row: ImportRow) -> None:
    """A purpose the user chose while reviewing is a confirmed decision, unlike a suggestion."""
    row.confidence = "confident"
    row.decision_source = "user_confirmed"
    row.classification_evidence = {
        **row.classification_evidence,
        "purpose": {"source": "user_confirmed"},
    }


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
    statement.statement_balance = parsed.statement_balance
    statement.total_amount_due = parsed.total_amount_due
    statement.minimum_amount_due = parsed.minimum_amount_due
    statement.available_credit = parsed.available_credit
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
                Account.instrument_type == parsed.statement_kind,
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
    history = confirmed_history(db, user_id)
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
    # A row is a proven re-import only when the same account already holds the same movement
    # (date, amount, narration, reference). Identical rows are matched one-to-one by ordinal so a
    # second genuine row is never absorbed by the first.
    existing_by_key: dict[tuple, list[tuple[int, Decimal | None]]] = {}
    if statement.account_id and parsed.transactions:
        for existing in db.execute(
            select(Transaction)
            .where(
                Transaction.user_id == user_id,
                Transaction.account_id == statement.account_id,
                Transaction.txn_date.in_({item.txn_date for item in parsed.transactions}),
            )
            .order_by(Transaction.id)
        ).scalars():
            key = movement_key(
                existing.txn_date, existing.amount, existing.description, existing.reference
            )
            existing_by_key.setdefault(key, []).append((existing.id, existing.bank_balance))
    claimed: set[int] = set()
    for transaction in parsed.transactions:
        key = movement_key(
            transaction.txn_date, transaction.amount, transaction.description, transaction.reference
        )
        # First unclaimed ledger row with the same movement whose running balance does not
        # contradict this row's. Two genuine rows differ in running balance even when all else
        # matches; with no balance on either side the match stays ambiguous (a re-import).
        duplicate = next(
            (
                candidate_id
                for candidate_id, candidate_balance in existing_by_key.get(key, [])
                if candidate_id not in claimed
                and (
                    candidate_balance is None
                    or transaction.balance is None
                    or candidate_balance == transaction.balance
                )
            ),
            None,
        )
        if duplicate is not None:
            claimed.add(duplicate)
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
        candidate = extract_counterparty(transaction.description, transaction.category)
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
        purpose_source = (
            {"source": "confirmed_rule", "rule_id": matched_rule.id}
            if matched_rule
            else {"source": "confirmed_counterparty"}
            if counterparty_purpose is not None
            else {"source": "normalized_history"}
            if suggested_purpose is not None
            else {"source": "unresolved"}
        )
        decision_source = (
            "confirmed_rule"
            if matched_rule or counterparty_purpose is not None
            else "history_suggestion"
            if suggested_purpose is not None
            else "built_in_mapping"
            if transaction.category != "uncategorized"
            else "unresolved"
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
                category=transaction.category,
                category_confidence=transaction.category_confidence,
                classification_evidence={
                    "category": transaction.classification_evidence or {},
                    "purpose": purpose_source,
                },
                decision_source=decision_source,
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


def unlock_statement(db: Session, statement: StatementImport, password: str, user_id: int) -> None:
    """Parse a protected PDF with a request-scoped password. The password is never stored."""
    parsed = parse_statement(settings.upload_dir / statement.storage_key, password=password)
    _stage_parsed(db, statement, parsed, user_id)
    db.commit()


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
        unlock_statement(db, statement, payload.password.get_secret_value(), user.id)
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


@router.get("/possibly-missed-rows", response_model=list[PossiblyMissedRow])
def possibly_missed_rows(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Read-only check of confirmed statements against the ledger; never re-imports anything."""
    report = []
    statements = db.scalars(
        select(StatementImport)
        .options(selectinload(StatementImport.rows))
        .where(
            StatementImport.user_id == user.id,
            StatementImport.status == ImportStatus.CONFIRMED,
            StatementImport.account_id.is_not(None),
        )
        .order_by(StatementImport.created_at, StatementImport.id)
    ).all()
    ledger_counts: dict[int, Counter] = {}
    for statement in statements:
        if statement.account_id not in ledger_counts:
            ledger_counts[statement.account_id] = Counter(
                movement_key(item.txn_date, item.amount, item.description, item.reference)
                for item in db.scalars(
                    select(Transaction).where(
                        Transaction.user_id == user.id,
                        Transaction.account_id == statement.account_id,
                    )
                )
            )
        groups: dict[tuple, list[ImportRow]] = {}
        # Rows the user deselected on purpose are not expected; system-flagged duplicates are.
        for row in sorted(statement.rows, key=lambda item: item.id):
            if row.selected or row.duplicate_of_id is not None:
                key = movement_key(row.txn_date, row.amount, row.description, row.reference)
                groups.setdefault(key, []).append(row)
        for key, rows in groups.items():
            present = ledger_counts[statement.account_id][key]
            if present < len(rows):
                report.append(
                    PossiblyMissedRow(
                        import_id=statement.id,
                        import_row_id=rows[present].id,
                        txn_date=rows[0].txn_date,
                        description=rows[0].description,
                        amount=rows[0].amount,
                        reference=rows[0].reference,
                        expected_count=len(rows),
                        present_count=present,
                        missing_count=len(rows) - present,
                    )
                )
    return report


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
    if "category" in changes:
        row.category_confidence = "confirmed"
        row.classification_evidence = {
            **row.classification_evidence,
            "category": {"source": "user_correction"},
        }
        row.decision_source = "user_confirmed"
    if "purpose" in changes:
        _mark_purpose_confirmed(row)
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
        if "category" in changes:
            row.category_confidence = "confirmed"
            row.classification_evidence = {
                **row.classification_evidence,
                "category": {"source": "user_correction"},
            }
            row.decision_source = "user_confirmed"
        if "purpose" in changes:
            _mark_purpose_confirmed(row)
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
        if (
            statement.instrument_confidence == "high"
            and account.instrument_type != statement.statement_kind
        ):
            raise HTTPException(
                status_code=422,
                detail="The selected account type does not match this statement",
            )
        if statement.instrument_confidence != "high":
            statement.statement_kind = account.instrument_type
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
                Account.instrument_type == statement.statement_kind,
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
            instrument_type=InstrumentType(statement.statement_kind),
            opening_balance=statement.opening_balance or 0,
            # Card statements cannot be reconciled yet; keep them out of the overall line.
            in_scope=statement.statement_kind != InstrumentType.CREDIT_CARD,
        )
        db.add(account)
        db.flush()
        statement.bank_slug = bank_slug
    imported = skipped = 0
    posted_movements = []
    ordered_rows = sorted(statement.rows, key=lambda item: item.id)
    row_ordinals = dict(
        zip(
            (item.id for item in ordered_rows),
            assign_ordinals(
                [
                    movement_key(item.txn_date, item.amount, item.description, item.reference)
                    for item in ordered_rows
                ]
            ),
            strict=True,
        )
    )
    # Ledger amounts that make up THIS statement: rows it posted plus rows proven to be re-imports
    # of movements already in the ledger. Rows the user deselected on purpose are excluded.
    statement_amounts: list[Decimal] = []
    for row in ordered_rows:
        if not row.selected:
            skipped += int(row.duplicate_of_id is not None)
            if row.duplicate_of_id is not None:
                matched_amount = db.scalar(
                    select(Transaction.amount).where(
                        Transaction.id == row.duplicate_of_id, Transaction.user_id == user.id
                    )
                )
                if matched_amount is not None:
                    statement_amounts.append(matched_amount)
            continue
        associations = row_associations[row.id]
        fingerprint = dedupe_hash(
            account.id,
            row.txn_date,
            row.amount,
            row.description,
            row.reference,
            row_ordinals[row.id],
        )
        ordinal = row_ordinals[row.id]
        already_posted = None
        while True:
            already_posted = db.execute(
                select(Transaction.amount, Transaction.bank_balance).where(
                    Transaction.user_id == user.id, Transaction.dedupe_hash == fingerprint
                )
            ).first()
            # Same slot taken by a row whose running balance proves it is a different genuine
            # movement: move to the next free ordinal instead of dropping this row.
            if (
                already_posted is not None
                and row.balance is not None
                and already_posted.bank_balance is not None
                and already_posted.bank_balance != row.balance
            ):
                ordinal += 1
                fingerprint = dedupe_hash(
                    account.id,
                    row.txn_date,
                    row.amount,
                    row.description,
                    row.reference,
                    ordinal,
                )
                continue
            break
        if already_posted is not None:
            skipped += 1
            statement_amounts.append(already_posted.amount)
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
            category=row.category,
            category_confidence=row.category_confidence,
            classification_evidence=row.classification_evidence,
            decision_source=row.decision_source,
            version=1,
            review_status="current",
        )
        db.add(movement)
        posted_movements.append(movement)
        statement_amounts.append(row.amount)
        imported += 1
    statement.account_id = account.id
    statement.status = ImportStatus.CONFIRMED
    db.flush()
    for movement in posted_movements:
        sync_transaction_group(db, movement)
    linked_card_payments = len(match_card_payments(db, user.id))
    linked_refunds = len(match_card_refunds(db, user.id))
    statement_difference = period_difference = None
    if (
        statement.period_start
        and statement.period_end
        and statement.opening_balance is not None
        and statement.closing_balance is not None
    ):
        # The statement is judged on its own movements; the whole-period view is reported
        # separately so rows from other statements never make this statement look wrong.
        _, statement_difference = calculate_reconciliation(
            statement.opening_balance, statement_amounts, statement.closing_balance
        )
        period_movements = list(
            db.scalars(
                select(Transaction.amount).where(
                    Transaction.user_id == user.id,
                    Transaction.account_id == account.id,
                    Transaction.txn_date.between(statement.period_start, statement.period_end),
                )
            )
        )
        _, period_difference = calculate_reconciliation(
            statement.opening_balance, period_movements, statement.closing_balance
        )
        record_reconciliation(db, statement, account.id, statement_difference)
    sync_cash_markers(db, user.id)
    db.commit()
    return ImportConfirmResponse(
        imported=imported,
        skipped_duplicates=skipped,
        account_id=account.id,
        linked_card_payments=linked_card_payments,
        linked_refunds=linked_refunds,
        statement_difference=statement_difference,
        period_difference=period_difference,
    )
