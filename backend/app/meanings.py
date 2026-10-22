from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Purpose,
    Reconciliation,
    Transaction,
    TransactionMeaning,
    TransactionMeaningChange,
)


def ensure_meaning(db: Session, transaction: Transaction) -> TransactionMeaning:
    if transaction.meaning:
        return transaction.meaning
    meaning = TransactionMeaning(
        transaction=transaction,
        user_id=transaction.user_id,
        purpose=transaction.purpose,
        person_id=transaction.person_id,
        employer_id=transaction.employer_id,
        trip_id=transaction.trip_id,
        counterparty_id=None,
        category="uncategorized",
        category_confidence="unresolved",
        classification_evidence={},
        decision_source="legacy_migration",
        notes=transaction.notes,
        version=1,
        review_status="current",
    )
    db.add(meaning)
    return meaning


def meaning_snapshot(meaning: TransactionMeaning) -> dict[str, str | int | None]:
    return {
        "purpose": meaning.purpose.value,
        "person_id": meaning.person_id,
        "employer_id": meaning.employer_id,
        "trip_id": meaning.trip_id,
        "counterparty_id": meaning.counterparty_id,
        "category": meaning.category,
        "notes": meaning.notes,
    }


def apply_meaning_change(
    db: Session,
    *,
    transaction: Transaction,
    actor_user_id: int,
    purpose: Purpose,
    person_id: int | None,
    employer_id: int | None,
    trip_id: int | None,
    counterparty_id: int | None,
    notes: str,
    category: str | None = None,
    expected_version: int | None = None,
    reason: str = "",
) -> TransactionMeaning:
    meaning = ensure_meaning(db, transaction)
    if expected_version is not None and expected_version != meaning.version:
        raise HTTPException(
            status_code=409,
            detail="This transaction changed after you opened it; reload and try again",
        )

    before = meaning_snapshot(meaning)
    category = category or meaning.category
    after = {
        "purpose": purpose.value,
        "person_id": person_id,
        "employer_id": employer_id,
        "trip_id": trip_id,
        "counterparty_id": counterparty_id,
        "category": category,
        "notes": notes,
    }
    if before == after:
        return meaning

    finalized = list(
        db.scalars(
            select(Reconciliation).where(
                Reconciliation.user_id == transaction.user_id,
                Reconciliation.account_id == transaction.account_id,
                Reconciliation.period_start <= transaction.txn_date,
                Reconciliation.period_end >= transaction.txn_date,
                Reconciliation.finalized_at.is_not(None),
            )
        )
    )
    reason = reason.strip()
    if finalized and not reason:
        raise HTTPException(
            status_code=422,
            detail="A correction reason is required for a finalized period",
        )

    meaning.purpose = purpose
    meaning.person_id = person_id
    meaning.employer_id = employer_id
    meaning.trip_id = trip_id
    meaning.counterparty_id = counterparty_id
    if category != meaning.category:
        meaning.category_confidence = "confirmed"
        meaning.classification_evidence = {"category": {"source": "user_correction"}}
        meaning.decision_source = "user_confirmed"
    meaning.category = category
    meaning.notes = notes
    meaning.version += 1
    if finalized:
        meaning.review_status = "needs_review"
        for reconciliation in finalized:
            reconciliation.classification_status = "needs_review"

    # Legacy columns remain synchronized until the rollback compatibility window closes.
    transaction.purpose = purpose
    transaction.person_id = person_id
    transaction.employer_id = employer_id
    transaction.trip_id = trip_id
    transaction.notes = notes
    db.add(
        TransactionMeaningChange(
            transaction_id=transaction.id,
            user_id=transaction.user_id,
            actor_user_id=actor_user_id,
            version=meaning.version,
            before_values=before,
            after_values=after,
            reason=reason,
        )
    )
    from app.grouping import sync_transaction_group

    sync_transaction_group(db, transaction)
    return meaning
