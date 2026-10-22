from datetime import date
from decimal import Decimal

import pytest

from app.parsers import ParseError
from app.parsers.banks.pnb import matches, parse_statement
from app.parsers.registry import parse_known_statement

TEXT = (
    "PNB ONE Statement of Account\nAccount No: 1234567890123456 for period: "
    "01-09-2026 to 30-09-2026\nDate Instrument ID Amount(INR) Type Balance Remarks"
)
HEADER = ["Date", "Instrument ID", "Amount(INR)", "Type", "Balance", "Remarks"]


def rows(*body):
    return [HEADER, *[list(row) for row in body]]


def test_direction_comes_from_the_type_column_and_balances_are_carried():
    parsed = parse_statement(
        TEXT,
        rows(
            ("02/09/2026", "", "100.50", "DR", "899.50", "UPI/SYNTH\nSHOP"),
            ("03/09/2026", "REF1", "2,000.00", "CR", "2,899.50", "SALARY SYNTH"),
        ),
    )

    assert [t.amount for t in parsed.transactions] == [Decimal("-100.50"), Decimal("2000.00")]
    assert parsed.transactions[0].description == "UPI/SYNTH SHOP"
    assert parsed.transactions[1].reference == "REF1"
    assert parsed.opening_balance == Decimal("1000.00")  # first balance minus its signed amount
    assert parsed.closing_balance == Decimal("2899.50")
    assert (parsed.period_start, parsed.period_end) == (date(2026, 9, 1), date(2026, 9, 30))
    assert (parsed.bank_slug, parsed.parser_name, parsed.source_hint) == (
        "pnb",
        "pnb_table",
        "90123456",
    )


def test_a_newest_first_listing_is_put_back_in_order():
    parsed = parse_statement(
        TEXT,
        rows(
            ("03/09/2026", "", "50.00", "CR", "950.00", "B"),
            ("02/09/2026", "", "100.00", "DR", "900.00", "A"),
        ),
    )

    assert [t.description for t in parsed.transactions] == ["A", "B"]
    assert parsed.opening_balance == Decimal("1000.00")


def test_a_row_without_a_clear_direction_stops_the_import():
    with pytest.raises(ParseError, match="no clear debit or credit marker"):
        parse_statement(TEXT, rows(("02/09/2026", "", "10.00", "", "90.00", "X")))


def test_unreadable_amounts_stop_the_import_and_footers_are_ignored():
    with pytest.raises(ParseError, match="could not be read"):
        parse_statement(TEXT, rows(("02/09/2026", "", "abc", "DR", "90.00", "X")))
    parsed = parse_statement(
        TEXT,
        rows(
            ("02/09/2026", "", "10.00", "DR", "90.00", "X"), ("Abbreviations", "", "", "", "", "")
        ),
    )
    assert len(parsed.transactions) == 1


def test_detection_needs_the_pnb_layout_not_just_the_word():
    assert matches(TEXT)
    assert not matches("Some other bank Statement of Account Instrument ID")
    assert not matches("PNB savings brochure")
    assert (
        parse_known_statement(TEXT, rows(("02/09/2026", "", "10.00", "DR", "90.00", "X"))).bank_slug
        == "pnb"
    )
