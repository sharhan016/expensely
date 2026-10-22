from decimal import Decimal

import pytest

from app.parsers import ParseError
from app.parsers.table import parse_table

HEADER = ["Date", "Narration", "Amount", "Type", "Balance"]


def test_a_single_amount_column_takes_its_direction_from_the_type_column():
    parsed = parse_table(
        [
            HEADER,
            ["02/09/2026", "SYNTH A", "100.50", "DR", "899.50"],
            ["03/09/2026", "SYNTH B", "2,000.00", "cr", "2,899.50"],
        ],
        "unknown",
    )

    assert [t.amount for t in parsed.transactions] == [Decimal("-100.50"), Decimal("2000.00")]
    assert parsed.opening_balance == Decimal("1000.00")
    assert parsed.closing_balance == Decimal("2899.50")


def test_a_missing_or_unknown_direction_stops_the_import_instead_of_guessing():
    with pytest.raises(ParseError, match="debit or credit"):
        parse_table([HEADER, ["02/09/2026", "SYNTH A", "100.50", "", "899.50"]], "unknown")
    with pytest.raises(ParseError, match="debit or credit"):
        parse_table([HEADER, ["02/09/2026", "SYNTH A", "100.50", "XX", "899.50"]], "unknown")


def test_layouts_with_separate_debit_and_credit_columns_are_unchanged():
    parsed = parse_table(
        [
            ["Date", "Narration", "Withdrawal", "Deposit", "Balance", "Tran Type"],
            ["02/09/2026", "SYNTH A", "100.00", "", "900.00", "TFR"],
        ],
        "unknown",
    )

    assert parsed.transactions[0].amount == Decimal("-100.00")
