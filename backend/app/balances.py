from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, ImportStatus, StatementImport, Transaction

# Statements arrive about monthly; a balance older than this is reported as stale, never as current.
STALE_AFTER_DAYS = 35


@dataclass(frozen=True)
class AccountBalance:
    account_id: int
    name: str
    instrument_type: str
    balance: Decimal
    # The balance is valid as of the end of the newest confirmed statement, not as of today.
    as_of: date | None
    statement_end: date | None
    age_days: int | None
    is_stale: bool


def account_balances(
    db: Session, user_id: int, *, today: date | None = None
) -> list[AccountBalance]:
    today = today or date.today()
    accounts = db.scalars(
        select(Account).where(Account.user_id == user_id).order_by(Account.name, Account.id)
    ).all()
    movement_totals: dict[int, Decimal] = defaultdict(Decimal)
    for account_id, amount in db.execute(
        select(Transaction.account_id, Transaction.amount).where(Transaction.user_id == user_id)
    ):
        movement_totals[account_id] += amount
    statement_ends = dict(
        db.execute(
            select(StatementImport.account_id, func.max(StatementImport.period_end))
            .where(
                StatementImport.user_id == user_id,
                StatementImport.status == ImportStatus.CONFIRMED,
                StatementImport.account_id.is_not(None),
            )
            .group_by(StatementImport.account_id)
        ).all()
    )
    balances = []
    for account in accounts:
        statement_end = statement_ends.get(account.id)
        age_days = (today - statement_end).days if statement_end else None
        balances.append(
            AccountBalance(
                account_id=account.id,
                name=account.name,
                instrument_type=str(account.instrument_type),
                balance=account.opening_balance + movement_totals[account.id],
                as_of=statement_end,
                statement_end=statement_end,
                age_days=age_days,
                is_stale=age_days is None or age_days > STALE_AFTER_DAYS,
            )
        )
    return balances


def overall_context(balances: list[AccountBalance]) -> dict:
    """Earliest account date, so one lagging account is never hidden behind a fresher one."""
    dated = [item.as_of for item in balances if item.as_of]
    as_of = min(dated) if dated else None
    ages = [item.age_days for item in balances if item.age_days is not None]
    return {
        "as_of": as_of.isoformat() if as_of else None,
        "age_days": max(ages) if ages else None,
        "is_stale": any(item.is_stale for item in balances),
        "accounts_without_statement": sum(item.as_of is None for item in balances),
    }


def money(value: Decimal) -> str:
    return f"{value:.2f}"


def balance_to_json(item: AccountBalance) -> dict:
    return {
        "account_id": item.account_id,
        "name": item.name,
        "instrument_type": item.instrument_type,
        "balance": money(item.balance),
        "as_of": item.as_of.isoformat() if item.as_of else None,
        "statement_end": item.statement_end.isoformat() if item.statement_end else None,
        "age_days": item.age_days,
        "is_stale": item.is_stale,
    }


def ledger_running_balances(db: Session, user_id: int, wanted: set[int]) -> dict[int, Decimal]:
    """Exact balance after each requested movement, independent of any page or filter.

    Per account: opening balance plus movements in (date, id) order, summed as Decimal.
    """
    if not wanted:
        return {}
    totals = {
        account_id: opening
        for account_id, opening in db.execute(
            select(Account.id, Account.opening_balance).where(Account.user_id == user_id)
        )
    }
    result: dict[int, Decimal] = {}
    for transaction_id, account_id, amount in db.execute(
        select(Transaction.id, Transaction.account_id, Transaction.amount)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.txn_date, Transaction.id)
    ):
        totals[account_id] = totals.get(account_id, Decimal("0")) + amount
        if transaction_id in wanted:
            result[transaction_id] = totals[account_id]
    return result
