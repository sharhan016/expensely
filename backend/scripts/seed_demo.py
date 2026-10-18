"""Create an optional local demo workspace without embedding credentials."""

import argparse
from datetime import date
from decimal import Decimal
from getpass import getpass

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Account, Employer, Person, Purpose, Transaction, Trip, User
from app.security import hash_password
from app.services import dedupe_hash


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="Jasmine Shah")
    args = parser.parse_args()
    password = getpass("Demo workspace password: ")
    if len(password) < 10:
        raise SystemExit("Password must contain at least 10 characters")
    with SessionLocal.begin() as db:
        if db.scalar(select(User).where(User.email == args.email.lower())):
            raise SystemExit("A user with that email already exists")
        user = User(
            email=args.email.lower(),
            display_name=args.name,
            password_hash=hash_password(password),
        )
        db.add(user)
        db.flush()
        account = Account(
            user_id=user.id,
            name="Demo salary account",
            bank_slug="demo",
            account_hint="4242",
            opening_balance=Decimal("50000"),
        )
        person = Person(user_id=user.id, name="Fatah Khan", relationship_label="Family")
        employer = Employer(user_id=user.id, name="Acuver Consulting", is_default=True)
        trip = Trip(
            user_id=user.id,
            name="Dubai client visit",
            starts_on=date(2026, 10, 1),
            ends_on=date(2026, 10, 7),
        )
        db.add_all([account, person, employer, trip])
        db.flush()
        samples = [
            (
                date(2026, 10, 1),
                "Entrusted funds received",
                Decimal("60000"),
                Purpose.ENTRUSTED,
                person.id,
                None,
                None,
            ),
            (
                date(2026, 10, 3),
                "Office software subscription",
                Decimal("-4320"),
                Purpose.BUSINESS,
                None,
                employer.id,
                None,
            ),
            (
                date(2026, 10, 4),
                "Client travel hotel",
                Decimal("-10000"),
                Purpose.BUSINESS,
                None,
                employer.id,
                trip.id,
            ),
            (
                date(2026, 10, 8),
                "Loan given",
                Decimal("-20000"),
                Purpose.LOAN_GIVEN,
                person.id,
                None,
                None,
            ),
        ]
        for txn_date, description, amount, purpose, person_id, employer_id, trip_id in samples:
            db.add(
                Transaction(
                    user_id=user.id,
                    account_id=account.id,
                    txn_date=txn_date,
                    description=description,
                    amount=amount,
                    dedupe_hash=dedupe_hash(account.id, txn_date, amount, description),
                    purpose=purpose,
                    person_id=person_id,
                    employer_id=employer_id,
                    trip_id=trip_id,
                )
            )
    print("Demo workspace created.")


if __name__ == "__main__":
    main()
