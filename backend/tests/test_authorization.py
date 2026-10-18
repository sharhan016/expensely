from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    Account,
    ClassificationRule,
    Employer,
    ImportBatch,
    ImportRow,
    ImportStatus,
    Person,
    Purpose,
    StatementImport,
    Transaction,
    Trip,
    User,
)
from app.ownership import validate_financial_associations
from app.security import create_session


@pytest.fixture
def api_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)

    owner = User(email="owner@example.test", password_hash="unused", display_name="Owner")
    stranger = User(email="stranger@example.test", password_hash="unused", display_name="Stranger")
    db.add_all([owner, stranger])
    db.flush()

    owner_account = Account(
        user_id=owner.id, name="Owner account", bank_slug="test", account_hint="1111"
    )
    stranger_account = Account(
        user_id=stranger.id, name="Stranger account", bank_slug="test", account_hint="2222"
    )
    owner_person = Person(user_id=owner.id, name="Owner person")
    stranger_person = Person(user_id=stranger.id, name="Stranger person")
    owner_employer = Employer(user_id=owner.id, name="Owner employer", is_default=True)
    stranger_employer = Employer(user_id=stranger.id, name="Stranger employer", is_default=True)
    owner_trip = Trip(
        user_id=owner.id,
        name="Owner trip",
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 1, 2),
    )
    stranger_trip = Trip(
        user_id=stranger.id,
        name="Stranger trip",
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 1, 2),
    )
    db.add_all(
        [
            owner_account,
            stranger_account,
            owner_person,
            stranger_person,
            owner_employer,
            stranger_employer,
            owner_trip,
            stranger_trip,
        ]
    )
    db.flush()

    transaction = Transaction(
        user_id=owner.id,
        account_id=owner_account.id,
        txn_date=date(2026, 1, 1),
        description="OWNER TRANSACTION",
        amount=Decimal("-10.00"),
        dedupe_hash="owner-transaction",
    )
    stranger_transaction = Transaction(
        user_id=stranger.id,
        account_id=stranger_account.id,
        txn_date=date(2026, 1, 1),
        description="STRANGER TRANSACTION",
        amount=Decimal("-20.00"),
        dedupe_hash="stranger-transaction",
    )
    batch = ImportBatch(user_id=owner.id)
    db.add_all([transaction, stranger_transaction, batch])
    db.flush()
    statement = StatementImport(
        batch_id=batch.id,
        user_id=owner.id,
        original_filename="statement.csv",
        storage_key="test/statement.csv",
        status=ImportStatus.READY,
    )
    statement.rows.extend(
        [
            ImportRow(
                txn_date=date(2026, 1, 2),
                description="ROW ONE",
                amount=Decimal("-5.00"),
            ),
            ImportRow(
                txn_date=date(2026, 1, 3),
                description="ROW TWO",
                amount=Decimal("-6.00"),
            ),
        ]
    )
    rule = ClassificationRule(
        user_id=owner.id,
        name="Owner rule",
        description_contains="OWNER",
        purpose=Purpose.PERSONAL,
    )
    db.add_all([statement, rule])
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        client.cookies.set("expense_session", create_session(owner.id))
        yield {
            "client": client,
            "db": db,
            "owner": owner,
            "owner_account": owner_account,
            "stranger_account": stranger_account,
            "owner_person": owner_person,
            "stranger_person": stranger_person,
            "owner_employer": owner_employer,
            "stranger_employer": stranger_employer,
            "owner_trip": owner_trip,
            "stranger_trip": stranger_trip,
            "transaction": transaction,
            "stranger_transaction": stranger_transaction,
            "statement": statement,
            "rows": list(statement.rows),
            "rule": rule,
        }
    app.dependency_overrides.clear()
    db.close()


@pytest.mark.parametrize(
    ("field", "foreign_key"),
    [
        ("person_id", "stranger_person"),
        ("employer_id", "stranger_employer"),
        ("trip_id", "stranger_trip"),
    ],
)
def test_transaction_update_rejects_foreign_associations(api_context, field, foreign_key):
    context = api_context
    response = context["client"].patch(
        f"/transactions/{context['transaction'].id}",
        json={field: context[foreign_key].id},
    )

    assert response.status_code == 404
    context["db"].refresh(context["transaction"])
    assert getattr(context["transaction"], field) is None


@pytest.mark.parametrize(
    ("field", "foreign_key"),
    [
        ("person_id", "stranger_person"),
        ("employer_id", "stranger_employer"),
        ("trip_id", "stranger_trip"),
    ],
)
def test_import_row_update_rejects_foreign_associations(api_context, field, foreign_key):
    context = api_context
    row = context["rows"][0]
    response = context["client"].patch(
        f"/imports/{context['statement'].id}/rows/{row.id}",
        json={field: context[foreign_key].id},
    )

    assert response.status_code == 404
    context["db"].refresh(row)
    assert getattr(row, field) is None


def test_bulk_import_update_is_atomic_when_association_is_foreign(api_context):
    context = api_context
    response = context["client"].patch(
        f"/imports/{context['statement'].id}/rows",
        json={
            "row_ids": [row.id for row in context["rows"]],
            "purpose": "business",
            "employer_id": context["stranger_employer"].id,
        },
    )

    assert response.status_code == 404
    for row in context["rows"]:
        context["db"].refresh(row)
        assert row.purpose == Purpose.OTHER
        assert row.employer_id is None


@pytest.mark.parametrize(
    ("field", "foreign_key"),
    [("person_id", "stranger_person"), ("employer_id", "stranger_employer")],
)
def test_rule_create_and_update_reject_foreign_associations(api_context, field, foreign_key):
    context = api_context
    value = context[foreign_key].id
    create_response = context["client"].post(
        "/rules",
        json={
            "name": f"Foreign {field}",
            "description_contains": "FOREIGN",
            "purpose": "personal",
            field: value,
        },
    )
    update_response = context["client"].patch(f"/rules/{context['rule'].id}", json={field: value})

    assert create_response.status_code == 404
    assert update_response.status_code == 404


def test_trip_decision_does_not_reveal_foreign_records(api_context):
    context = api_context
    foreign_trip = context["client"].patch(
        f"/trips/{context['stranger_trip'].id}/transactions/{context['transaction'].id}",
        json={"status": "included"},
    )
    foreign_transaction = context["client"].patch(
        f"/trips/{context['owner_trip'].id}/transactions/{context['stranger_transaction'].id}",
        json={"status": "included"},
    )

    assert foreign_trip.status_code == 404
    assert foreign_transaction.status_code == 404


def test_import_confirmation_revalidates_row_and_account_ownership(api_context):
    context = api_context
    foreign_account = context["client"].post(
        f"/imports/{context['statement'].id}/confirm",
        json={"account_id": context["stranger_account"].id},
    )
    assert foreign_account.status_code == 404

    row = context["rows"][0]
    row.person_id = context["stranger_person"].id
    context["db"].commit()
    poisoned_row = context["client"].post(
        f"/imports/{context['statement'].id}/confirm",
        json={"account_id": context["owner_account"].id},
    )

    assert poisoned_row.status_code == 404
    assert context["statement"].status == ImportStatus.READY


def test_required_associations_and_default_employer_policy(api_context):
    context = api_context
    db = context["db"]

    business = validate_financial_associations(
        db,
        user_id=context["owner"].id,
        purpose=Purpose.BUSINESS,
        person_id=None,
        employer_id=None,
    )
    assert business.employer_id == context["owner_employer"].id

    for purpose in (Purpose.ENTRUSTED, Purpose.LOAN_GIVEN, Purpose.LOAN_REPAYMENT):
        with pytest.raises(HTTPException) as exc_info:
            validate_financial_associations(
                db,
                user_id=context["owner"].id,
                purpose=purpose,
                person_id=None,
                employer_id=None,
            )
        assert exc_info.value.status_code == 422


def test_owned_associations_remain_usable(api_context):
    context = api_context
    response = context["client"].patch(
        f"/transactions/{context['transaction'].id}",
        json={
            "purpose": "loan_given",
            "person_id": context["owner_person"].id,
            "trip_id": context["owner_trip"].id,
        },
    )

    assert response.status_code == 200
    assert response.json()["person_id"] == context["owner_person"].id
    assert response.json()["trip_id"] == context["owner_trip"].id


def test_replacing_default_employer_keeps_one_default(api_context):
    context = api_context
    response = context["client"].post(
        "/employers", json={"name": "New default employer", "is_default": True}
    )

    assert response.status_code == 201
    context["db"].refresh(context["owner_employer"])
    assert context["owner_employer"].is_default is False
    defaults = [
        employer
        for employer in context["db"].query(Employer).filter_by(user_id=context["owner"].id)
        if employer.is_default
    ]
    assert [employer.name for employer in defaults] == ["New default employer"]
