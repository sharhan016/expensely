from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.models import ImportBatch, ImportRow, ImportStatus, StatementImport, Transaction

DAY = date(2026, 3, 5)


def _count(workspace, account) -> int:
    return workspace.db.scalar(
        select(func.count(Transaction.id)).where(Transaction.account_id == account.id)
    )


def test_identical_rows_in_one_statement_are_both_kept(workspace):
    account = workspace.account()
    statement = workspace.stage(
        [(DAY, "SYNTHETIC FEE", "-10.00", ""), (DAY, "SYNTHETIC FEE", "-10.00", "")]
    )

    assert all(row.duplicate_of_id is None for row in statement.rows)
    response = workspace.confirm(statement, account)

    assert response.json()["imported"] == 2
    assert response.json()["skipped_duplicates"] == 0
    assert _count(workspace, account) == 2


def test_identical_row_in_another_account_is_not_a_duplicate(workspace):
    first = workspace.account("1111")
    second = workspace.account("2222")
    workspace.confirm(workspace.stage([(DAY, "SYNTHETIC FEE", "-10.00", "")], hint="1111"), first)

    statement = workspace.stage([(DAY, "SYNTHETIC FEE", "-10.00", "")], hint="2222")

    assert statement.rows[0].duplicate_of_id is None
    assert statement.rows[0].selected is True
    assert workspace.confirm(statement, second).json()["imported"] == 1


def test_same_narration_with_a_different_reference_is_not_a_duplicate(workspace):
    account = workspace.account()
    workspace.confirm(workspace.stage([(DAY, "SYNTHETIC FEE", "-10.00", "REF-A")]), account)

    statement = workspace.stage([(DAY, "SYNTHETIC FEE", "-10.00", "REF-B")])

    assert statement.rows[0].duplicate_of_id is None


def test_reuploading_the_same_statement_posts_nothing_new(workspace):
    account = workspace.account()
    rows = [(DAY, "SYNTHETIC FEE", "-10.00", ""), (DAY, "SYNTHETIC FEE", "-10.00", "")]
    workspace.confirm(workspace.stage(rows), account)

    again = workspace.stage(rows)

    assert all(row.duplicate_of_id is not None and not row.selected for row in again.rows)
    response = workspace.confirm(again, account)
    assert response.json()["imported"] == 0
    assert _count(workspace, account) == 2


def test_overlapping_statement_posts_only_the_new_rows(workspace):
    account = workspace.account()
    workspace.confirm(
        workspace.stage(
            [(DAY, "SYNTHETIC FEE", "-10.00", ""), (DAY, "SYNTHETIC FEE", "-10.00", "")]
        ),
        account,
    )

    overlap = workspace.stage(
        [
            (DAY, "SYNTHETIC FEE", "-10.00", ""),
            (DAY, "SYNTHETIC FEE", "-10.00", ""),
            (DAY, "SYNTHETIC FEE", "-10.00", ""),
            (date(2026, 3, 6), "SYNTHETIC SHOP", "-4.00", ""),
        ]
    )

    response = workspace.confirm(overlap, account)
    # Two proven re-imports are skipped; the third identical row and the new row are kept.
    assert response.json()["imported"] == 2
    assert _count(workspace, account) == 4


def test_legacy_hashes_still_match_as_proven_reimports(workspace):
    from app.services import dedupe_hash

    account = workspace.account()
    legacy = Transaction(
        user_id=workspace.owner.id,
        account_id=account.id,
        txn_date=DAY,
        description="SYNTHETIC FEE",
        amount=Decimal("-10.00"),
        reference="",
        dedupe_hash=dedupe_hash(account.id, DAY, Decimal("-10.00"), "SYNTHETIC FEE", ""),
    )
    workspace.db.add(legacy)
    workspace.db.commit()

    statement = workspace.stage([(DAY, "SYNTHETIC FEE", "-10.00", "")])
    assert statement.rows[0].duplicate_of_id == legacy.id
    assert workspace.confirm(statement, account).json()["imported"] == 0
    assert _count(workspace, account) == 1


def _legacy_statement(workspace, user, account, rows, posted):
    """Confirmed statement whose rows were partly dropped by the old duplicate rules."""
    batch = ImportBatch(user_id=user.id)
    workspace.db.add(batch)
    workspace.db.flush()
    statement = StatementImport(
        batch_id=batch.id,
        user_id=user.id,
        account_id=account.id,
        original_filename="synthetic.csv",
        storage_key=f"{user.id}/synthetic.csv",
        status=ImportStatus.CONFIRMED,
    )
    statement.rows.extend(rows)
    workspace.db.add(statement)
    workspace.db.flush()
    for index, (txn_date, description, amount) in enumerate(posted):
        workspace.db.add(
            Transaction(
                user_id=user.id,
                account_id=account.id,
                statement_import_id=statement.id,
                txn_date=txn_date,
                description=description,
                amount=Decimal(amount),
                dedupe_hash=f"legacy-{statement.id}-{index}",
            )
        )
    workspace.db.commit()
    return statement


def test_possibly_missed_rows_report_is_owned_and_read_only(workspace):
    account = workspace.account()
    foreign_account = workspace.account("2222", user=workspace.stranger)
    identical = [
        ImportRow(
            txn_date=DAY, description="SYNTHETIC FEE", amount=Decimal("-10.00"), selected=True
        )
        for _ in range(2)
    ]
    statement = _legacy_statement(
        workspace, workspace.owner, account, identical, [(DAY, "SYNTHETIC FEE", "-10.00")]
    )
    # Fully posted rows must not appear in the report.
    _legacy_statement(
        workspace,
        workspace.owner,
        account,
        [ImportRow(txn_date=DAY, description="SYNTHETIC SHOP", amount=Decimal("-4.00"))],
        [(DAY, "SYNTHETIC SHOP", "-4.00")],
    )
    _legacy_statement(
        workspace,
        workspace.stranger,
        foreign_account,
        [ImportRow(txn_date=DAY, description="STRANGER ROW", amount=Decimal("-1.00"))],
        [],
    )
    before = workspace.db.scalar(select(func.count(Transaction.id)))
    rows_before = workspace.db.scalar(select(func.count(ImportRow.id)))

    report = workspace.client.get("/imports/possibly-missed-rows")

    assert report.status_code == 200
    items = report.json()
    assert len(items) == 1
    assert items[0]["import_id"] == str(statement.id)
    assert items[0]["missing_count"] == 1
    assert items[0]["expected_count"] == 2
    assert items[0]["present_count"] == 1
    assert workspace.db.scalar(select(func.count(Transaction.id))) == before
    assert workspace.db.scalar(select(func.count(ImportRow.id))) == rows_before


def test_split_identical_pair_with_different_running_balances_keeps_both(workspace):
    """Statement A ends on the day the pair was split; statement B starts that same day."""
    account = workspace.account()
    first = workspace.stage(
        [(DAY, "SYNTHETIC FEE", "-10.00", "", "990.00")], period=(date(2026, 3, 1), DAY)
    )
    workspace.confirm(first, account)

    second = workspace.stage(
        [
            (DAY, "SYNTHETIC FEE", "-10.00", "", "980.00"),
            (date(2026, 3, 6), "SYNTHETIC SHOP", "-4.00", "", "976.00"),
        ],
        period=(DAY, date(2026, 3, 10)),
    )

    assert second.rows[0].duplicate_of_id is None
    assert workspace.confirm(second, account).json()["imported"] == 2
    assert _count(workspace, account) == 3

    again = workspace.stage(
        [(DAY, "SYNTHETIC FEE", "-10.00", "", "980.00")], period=(DAY, date(2026, 3, 10))
    )
    assert again.rows[0].duplicate_of_id is not None
    assert workspace.confirm(again, account).json()["imported"] == 0
    assert _count(workspace, account) == 3


def test_split_identical_pair_without_balances_is_treated_as_a_reimport(workspace):
    """Known limit: with no balance to tell them apart, one row seen twice is a re-import."""
    account = workspace.account()
    workspace.confirm(workspace.stage([(DAY, "SYNTHETIC FEE", "-10.00", "")]), account)

    second = workspace.stage([(DAY, "SYNTHETIC FEE", "-10.00", "")])

    assert second.rows[0].duplicate_of_id is not None
    assert second.rows[0].selected is False
