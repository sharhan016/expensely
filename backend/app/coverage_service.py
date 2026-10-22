"""Database side of coverage: statement spans, reconciliation evidence and re-checking.

The arithmetic and chain rules live in `app.coverage`; this module only reads the ledger,
fingerprints movement sets and records what was checked. Nothing here changes a movement.
"""

import hashlib
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.coverage import (
    AccountCoverage,
    AccountEntry,
    StatementSpan,
    compute_coverage,
    date_label,
    describe_account,
    describe_overall,
    overall_coverage,
)
from app.models import (
    Account,
    AcknowledgedGap,
    CoverageGapUnbridge,
    ImportStatus,
    Reconciliation,
    StatementImport,
    Transaction,
)
from app.services import calculate_reconciliation, movement_key

INVALIDATION_REASON = (
    "A movement in this period was added, removed or changed after it was checked."
)


def movement_fingerprint(db: Session, user_id: int, account_id: int, start: date, end: date) -> str:
    """Stable digest of the account's movements inside a period (ids, dates, exact amounts)."""
    digest = hashlib.sha256()
    for transaction_id, txn_date, amount in db.execute(
        select(Transaction.id, Transaction.txn_date, Transaction.amount)
        .where(
            Transaction.user_id == user_id,
            Transaction.account_id == account_id,
            Transaction.txn_date.between(start, end),
        )
        .order_by(Transaction.id)
    ):
        digest.update(f"{transaction_id}|{txn_date.isoformat()}|{amount:.2f};".encode())
    return digest.hexdigest()


def statement_ledger_amounts(db: Session, statement: StatementImport) -> list[Decimal]:
    """Ledger amounts that make up one confirmed statement, rebuilt from its rows.

    A row counts through the movement it posted or the existing movement it was proven to
    re-import. Rows the user deselected on purpose are not part of the statement. A row with no
    ledger match contributes nothing, so the difference stays visible.
    """
    candidates = list(
        db.scalars(
            select(Transaction)
            .where(
                Transaction.user_id == statement.user_id,
                Transaction.account_id == statement.account_id,
            )
            .order_by(Transaction.id)
        )
    )
    by_id = {item.id: item for item in candidates}
    from_this_statement = [item for item in candidates if item.statement_import_id == statement.id]
    claimed: set[int] = set()
    amounts: list[Decimal] = []
    for row in sorted(statement.rows, key=lambda item: item.id):
        if not row.selected and row.duplicate_of_id is None:
            continue
        match = by_id.get(row.duplicate_of_id) if row.duplicate_of_id else None
        if match is None or match.id in claimed:
            key = movement_key(row.txn_date, row.amount, row.description, row.reference)
            for pool in (from_this_statement, candidates):
                match = next(
                    (
                        item
                        for item in pool
                        if item.id not in claimed
                        and movement_key(
                            item.txn_date, item.amount, item.description, item.reference
                        )
                        == key
                    ),
                    None,
                )
                if match:
                    break
        if match is not None:
            claimed.add(match.id)
            amounts.append(match.amount)
    return amounts


def record_reconciliation(
    db: Session,
    statement: StatementImport,
    account_id: int,
    statement_difference: Decimal,
) -> Reconciliation:
    """Create or refresh the evidence row for a statement period. Idempotent."""
    reconciliation = db.scalar(
        select(Reconciliation).where(
            Reconciliation.user_id == statement.user_id,
            Reconciliation.account_id == account_id,
            Reconciliation.period_start == statement.period_start,
            Reconciliation.period_end == statement.period_end,
        )
    )
    if not reconciliation:
        reconciliation = Reconciliation(
            user_id=statement.user_id,
            account_id=account_id,
            period_start=statement.period_start,
            period_end=statement.period_end,
            opening_balance=statement.opening_balance,
            statement_closing_balance=statement.closing_balance,
        )
        db.add(reconciliation)
    reconciliation.opening_balance = statement.opening_balance
    reconciliation.statement_closing_balance = statement.closing_balance
    reconciliation.status = "balanced" if statement_difference == 0 else "difference"
    reconciliation.source_statement_id = statement.id
    reconciliation.computed_difference = statement_difference
    period_amounts = list(
        db.scalars(
            select(Transaction.amount).where(
                Transaction.user_id == statement.user_id,
                Transaction.account_id == account_id,
                Transaction.txn_date.between(statement.period_start, statement.period_end),
            )
        )
    )
    _, reconciliation.period_difference = calculate_reconciliation(
        statement.opening_balance, period_amounts, statement.closing_balance
    )
    reconciliation.movement_fingerprint = movement_fingerprint(
        db, statement.user_id, account_id, statement.period_start, statement.period_end
    )
    reconciliation.invalidated_at = None
    reconciliation.invalidation_reason = None
    return reconciliation


def recompute_reconciliations(db: Session, user_id: int) -> dict[str, int]:
    """Re-check every confirmed statement against the ledger as it is now. Idempotent."""
    statements = db.scalars(
        select(StatementImport)
        .options(selectinload(StatementImport.rows))
        .where(
            StatementImport.user_id == user_id,
            StatementImport.status == ImportStatus.CONFIRMED,
            StatementImport.account_id.is_not(None),
            StatementImport.period_start.is_not(None),
            StatementImport.period_end.is_not(None),
            StatementImport.opening_balance.is_not(None),
            StatementImport.closing_balance.is_not(None),
        )
        .order_by(StatementImport.created_at, StatementImport.id)
    ).all()
    rechecked = balanced = difference = 0
    for statement in statements:
        _, statement_difference = calculate_reconciliation(
            statement.opening_balance,
            statement_ledger_amounts(db, statement),
            statement.closing_balance,
        )
        record_reconciliation(db, statement, statement.account_id, statement_difference)
        rechecked += 1
        balanced += statement_difference == 0
        difference += statement_difference != 0
    db.flush()
    return {"rechecked": rechecked, "balanced": balanced, "difference": difference}


def _span_state(db: Session, statement: StatementImport, reconciliation: Reconciliation | None):
    if statement.opening_balance is None or statement.closing_balance is None:
        return "unverifiable"
    if reconciliation is None or reconciliation.movement_fingerprint is None:
        return "unverified"
    if reconciliation.invalidated_at is not None:
        return "invalidated"
    current = movement_fingerprint(
        db, statement.user_id, statement.account_id, statement.period_start, statement.period_end
    )
    if current != reconciliation.movement_fingerprint:
        return "invalidated"
    if reconciliation.status != "balanced":
        return "difference"
    if reconciliation.period_difference is None:
        return "unverified"
    # The statement adds up on its own rows, but other movements in the same dates do not.
    return "balanced" if reconciliation.period_difference == 0 else "period_mismatch"


def account_spans(db: Session, account: Account) -> list[StatementSpan]:
    statements = db.scalars(
        select(StatementImport)
        .where(
            StatementImport.user_id == account.user_id,
            StatementImport.account_id == account.id,
            StatementImport.status == ImportStatus.CONFIRMED,
            StatementImport.period_start.is_not(None),
            StatementImport.period_end.is_not(None),
        )
        .order_by(StatementImport.created_at, StatementImport.id)
    ).all()
    reconciliations = {
        (item.period_start, item.period_end): item
        for item in db.scalars(
            select(Reconciliation).where(
                Reconciliation.user_id == account.user_id, Reconciliation.account_id == account.id
            )
        )
    }
    chosen: dict[tuple[date, date], StatementImport] = {}
    for statement in statements:
        period = (statement.period_start, statement.period_end)
        recorded = reconciliations.get(period)
        if period not in chosen or (
            recorded is not None and recorded.source_statement_id == statement.id
        ):
            chosen[period] = statement
    return [
        StatementSpan(
            key=str(statement.id),
            start=period[0],
            end=period[1],
            opening=statement.opening_balance,
            closing=statement.closing_balance,
            state=_span_state(db, statement, reconciliations.get(period)),
        )
        for period, statement in chosen.items()
    ]


def account_entry(db: Session, account: Account) -> AccountEntry:
    unbridged = frozenset(
        (item.gap_start, item.gap_end)
        for item in db.scalars(
            select(CoverageGapUnbridge).where(
                CoverageGapUnbridge.user_id == account.user_id,
                CoverageGapUnbridge.account_id == account.id,
            )
        )
    )
    acknowledged = frozenset(
        (item.gap_start, item.gap_end)
        for item in db.scalars(
            select(AcknowledgedGap).where(
                AcknowledgedGap.user_id == account.user_id,
                AcknowledgedGap.account_id == account.id,
            )
        )
    )
    coverage = compute_coverage(
        account_spans(db, account),
        acknowledged=acknowledged,
        records_start=account.records_start,
        anchor_opening=account.records_opening_balance,
        unbridged=unbridged,
    )
    return AccountEntry(account.id, account.name, account.in_scope, coverage)


def _segment_json(segment) -> dict:
    return {
        "kind": segment.kind,
        "start": segment.start.isoformat(),
        "end": segment.end.isoformat(),
        "days": (segment.end - segment.start).days + 1,
        "detail": segment.detail,
        "statement_id": segment.key,
        "unbridged": segment.unbridged,
    }


def _account_json(entry: AccountEntry, account: Account, coverage: AccountCoverage, today: date):
    return {
        "account_id": account.id,
        "name": account.name,
        "instrument_type": str(account.instrument_type),
        "in_scope": account.in_scope,
        "records_start": coverage.records_start.isoformat() if coverage.records_start else None,
        "records_start_is_default": account.records_start is None,
        "records_opening_balance": (
            f"{account.records_opening_balance:.2f}"
            if account.records_opening_balance is not None
            else None
        ),
        "reconciled_through": (
            coverage.reconciled_through.isoformat() if coverage.reconciled_through else None
        ),
        "covered": [[start.isoformat(), end.isoformat()] for start, end in coverage.covered],
        "segments": [_segment_json(segment) for segment in coverage.segments],
        "first_problem": _segment_json(coverage.first_problem) if coverage.first_problem else None,
        "summary": describe_account(account.name, coverage, today=today),
    }


def coverage_report(db: Session, user_id: int, *, today: date | None = None) -> dict:
    today = today or date.today()
    accounts = db.scalars(
        select(Account).where(Account.user_id == user_id).order_by(Account.name, Account.id)
    ).all()
    entries = [account_entry(db, account) for account in accounts]
    overall = overall_coverage(entries)
    return {
        "as_of_today": today.isoformat(),
        "overall": {
            "reconciled_through": (
                overall.reconciled_through.isoformat() if overall.reconciled_through else None
            ),
            "lagging_account_id": overall.lagging_account_id,
            "lagging_name": overall.lagging_name,
            "in_scope_count": overall.in_scope_count,
            "summary": describe_overall(overall, today=today),
            "banner": _banner(entries, overall, today),
        },
        "accounts": [
            _account_json(entry, account, entry.coverage, today)
            for entry, account in zip(entries, accounts, strict=True)
        ],
    }


def _banner(entries: list[AccountEntry], overall, today: date) -> str:
    """One plain line: overall status plus the first thing missing for the lagging account."""
    text = describe_overall(overall, today=today)
    laggard = next(
        (entry for entry in entries if entry.account_id == overall.lagging_account_id), None
    )
    problem = laggard.coverage.first_problem if laggard else None
    if problem is not None:
        span = (
            date_label(problem.start)
            if problem.start == problem.end
            else f"{date_label(problem.start)} to {date_label(problem.end)}"
        )
        text += f" Next problem ({laggard.name}): {problem.kind.replace('_', ' ')} at {span}."
    # Card statements have no closing balance to check yet; say so rather than imply they are.
    return f"{text} Credit cards are not checked yet."


def coverage_sentence(db: Session, user_id: int, *, today: date | None = None) -> str:
    today = today or date.today()
    accounts = db.scalars(select(Account).where(Account.user_id == user_id)).all()
    entries = [account_entry(db, account) for account in accounts]
    return _banner(entries, overall_coverage(entries), today)
