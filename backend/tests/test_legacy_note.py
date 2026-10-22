from datetime import date
from decimal import Decimal

from app.models import Purpose, Transaction, TransactionMeaning
from app.services import legacy_unconfirmed_count


def _entry(workspace, account, source: str, purpose: Purpose, index: int, user=None):
    owner = user or workspace.owner
    transaction = Transaction(
        user_id=owner.id,
        account_id=account.id,
        txn_date=date(2026, 2, 1),
        description=f"SYNTHETIC ENTRY {index}",
        amount=Decimal("-5.00"),
        purpose=purpose,
        dedupe_hash=f"legacy-note-{owner.id}-{index}",
    )
    transaction.meaning = TransactionMeaning(
        user_id=owner.id, purpose=purpose, decision_source=source
    )
    workspace.db.add(transaction)
    workspace.db.commit()


def test_counts_only_older_entries_that_would_have_taught_suggestions(workspace):
    account = workspace.account()
    foreign = workspace.account(user=workspace.stranger)
    _entry(workspace, account, "legacy_migration", Purpose.PERSONAL, 1)
    _entry(workspace, account, "legacy_migration", Purpose.OTHER, 2)  # never a suggestion
    _entry(workspace, account, "user_confirmed", Purpose.PERSONAL, 3)
    _entry(workspace, foreign, "legacy_migration", Purpose.PERSONAL, 4, user=workspace.stranger)

    assert legacy_unconfirmed_count(workspace.db, workspace.owner.id) == 1


def test_assistant_explains_older_entries_in_plain_language(workspace):
    account = workspace.account()
    for index in range(2):
        _entry(workspace, account, "legacy_migration", Purpose.PERSONAL, index)

    reply = workspace.client.post("/assistant/turn", json={"message": "balance summary"}).json()

    assert "2 older entries are not used for suggestions until you confirm them" in reply["content"]


def test_assistant_uses_singular_wording_and_is_silent_when_there_are_none(workspace):
    account = workspace.account()
    quiet = workspace.client.post("/assistant/turn", json={"message": "balance summary"}).json()
    assert "older entr" not in quiet["content"]

    _entry(workspace, account, "legacy_migration", Purpose.PERSONAL, 1)
    reply = workspace.client.post("/assistant/turn", json={"message": "unresolved"}).json()

    assert "1 older entry is not used for suggestions until you confirm it" in reply["content"]


def test_note_never_changes_the_entries(workspace):
    account = workspace.account()
    _entry(workspace, account, "legacy_migration", Purpose.PERSONAL, 1)

    workspace.client.post("/assistant/turn", json={"message": "balance summary"})

    meaning = workspace.db.query(TransactionMeaning).one()
    assert meaning.decision_source == "legacy_migration"
    assert meaning.purpose == Purpose.PERSONAL
