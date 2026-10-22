"""Invalidate reconciled periods when a bank movement is added, removed or changed.

Registered once at import. Meaning edits live on TransactionMeaning and never touch these
columns, so they keep their existing behavior.
"""

from sqlalchemy import event, inspect, update
from sqlalchemy.orm import Session

from app.coverage_service import INVALIDATION_REASON
from app.models import Reconciliation, Transaction, utc_now

WATCHED_FIELDS = ("amount", "txn_date", "account_id")


def _touched_days(session: Session) -> set[tuple[int, int, object]]:
    touched: set[tuple[int, int, object]] = set()

    def add(user_id, account_id, day):
        if user_id is not None and account_id is not None and day is not None:
            touched.add((user_id, account_id, day))

    for item in (*session.new, *session.deleted):
        if isinstance(item, Transaction):
            add(item.user_id, item.account_id, item.txn_date)
    for item in session.dirty:
        if not isinstance(item, Transaction):
            continue
        state = inspect(item)
        changes = {name: state.attrs[name].history for name in WATCHED_FIELDS}
        if not any(history.has_changes() for history in changes.values()):
            continue
        accounts = {*changes["account_id"].deleted, *changes["account_id"].added}
        days = {*changes["txn_date"].deleted, *changes["txn_date"].added}
        accounts = accounts or {item.account_id}
        days = days or {item.txn_date}
        for account_id in accounts:
            for day in days:
                add(item.user_id, account_id, day)
    return touched


@event.listens_for(Session, "before_flush")
def invalidate_reconciled_periods(session: Session, flush_context, instances) -> None:
    if session.info.get("skip_coverage_invalidation"):
        return  # loaders running against an older schema (migration tests) opt out explicitly
    touched = _touched_days(session)
    if not touched:
        return
    with session.no_autoflush:
        for user_id, account_id, day in touched:
            session.execute(
                update(Reconciliation)
                .where(
                    Reconciliation.user_id == user_id,
                    Reconciliation.account_id == account_id,
                    Reconciliation.period_start <= day,
                    Reconciliation.period_end >= day,
                    Reconciliation.movement_fingerprint.is_not(None),
                    Reconciliation.invalidated_at.is_(None),
                )
                .values(invalidated_at=utc_now(), invalidation_reason=INVALIDATION_REASON)
            )
