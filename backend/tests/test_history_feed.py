from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import Purpose, Transaction, TransactionMeaning

DESCRIPTION = "SYNTHETIC GYM MONTHLY"
SEEN, LATER = date(2026, 2, 1), date(2026, 3, 1)


def _posted(workspace, account, decision_source: str):
    transaction = Transaction(
        user_id=workspace.owner.id,
        account_id=account.id,
        txn_date=SEEN,
        description=DESCRIPTION,
        amount=Decimal("-20.00"),
        purpose=Purpose.PERSONAL,
        dedupe_hash=f"history-{decision_source}",
    )
    transaction.meaning = TransactionMeaning(
        user_id=workspace.owner.id,
        purpose=Purpose.PERSONAL,
        decision_source=decision_source,
    )
    workspace.db.add(transaction)
    workspace.db.commit()
    return transaction


def _next_purpose(workspace):
    statement = workspace.stage([(LATER, DESCRIPTION, "-20.00", "")])
    return statement.rows[0]


def test_unreviewed_suggestion_does_not_seed_the_next_suggestion(workspace):
    account = workspace.account()
    _posted(workspace, account, "history_suggestion")

    row = _next_purpose(workspace)

    assert row.purpose == Purpose.OTHER
    assert row.decision_source == "unresolved"


def test_built_in_and_unresolved_decisions_do_not_seed_suggestions(workspace):
    account = workspace.account()
    _posted(workspace, account, "built_in_mapping")

    assert _next_purpose(workspace).purpose == Purpose.OTHER


def test_user_confirmed_decision_seeds_the_next_suggestion(workspace):
    account = workspace.account()
    _posted(workspace, account, "user_confirmed")

    row = _next_purpose(workspace)

    assert row.purpose == Purpose.PERSONAL
    assert row.decision_source == "history_suggestion"


def test_purpose_chosen_during_review_becomes_confirmed_history(workspace):
    account = workspace.account()
    first = workspace.stage([(SEEN, DESCRIPTION, "-20.00", "")])
    edit = workspace.client.patch(
        f"/imports/{first.id}/rows/{first.rows[0].id}", json={"purpose": "personal"}
    )
    assert edit.status_code == 200
    assert workspace.confirm(first, account).status_code == 200

    assert _next_purpose(workspace).purpose == Purpose.PERSONAL


def test_unreviewed_row_confirmed_in_bulk_does_not_become_history(workspace):
    account = workspace.account()
    seeded = _posted(workspace, account, "user_confirmed")
    reviewed = workspace.stage([(LATER, DESCRIPTION, "-20.00", "")])
    assert reviewed.rows[0].purpose == Purpose.PERSONAL  # suggestion shown, never edited
    workspace.confirm(reviewed, account)
    # The user later corrects the original; the unreviewed suggestion must not outlive it.
    workspace.db.delete(seeded.meaning)
    workspace.db.delete(seeded)
    workspace.db.commit()

    assert _next_purpose(workspace).purpose == Purpose.OTHER


def test_correcting_a_posted_movement_confirms_its_purpose(workspace):
    account = workspace.account()
    posted = _posted(workspace, account, "unresolved")
    posted.meaning.purpose = Purpose.OTHER
    posted.purpose = Purpose.OTHER
    workspace.db.commit()
    response = workspace.client.patch(
        f"/transactions/{posted.id}",
        json={"purpose": "personal", "notes": "", "expected_version": 1},
    )
    assert response.status_code == 200
    meaning = workspace.db.scalar(select(TransactionMeaning))
    assert meaning.decision_source == "user_confirmed"

    assert _next_purpose(workspace).purpose == Purpose.PERSONAL
