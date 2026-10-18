from decimal import Decimal

import pytest

from app.parsers.normalise import parse_amount, parse_date, parse_signed_amount
from app.parsers.statement import ParseError, parse_statement


def test_indian_amount_formats_are_exact():
    assert parse_amount("₹1,20,450.00") == Decimal("120450.00")
    assert parse_amount("1,234.00 Dr") == Decimal("-1234.00")
    assert parse_amount("(800.25)") == Decimal("-800.25")
    assert parse_signed_amount(debit="1,500.00") == Decimal("-1500.00")
    assert parse_signed_amount(credit="2,000.00") == Decimal("2000.00")


def test_indian_dates_are_day_first():
    assert parse_date("03/04/2024").isoformat() == "2024-04-03"
    assert parse_date("03-Apr-24").isoformat() == "2024-04-03"


def test_csv_statement_extracts_signed_rows(tmp_path):
    statement = tmp_path / "hdfc.csv"
    statement.write_text(
        "HDFC Bank Statement,,,,\n"
        "Date,Narration,Withdrawal,Deposit,Closing Balance\n"
        "01/04/2024,UPI WATER,160.00,,99840.00\n"
        "02/04/2024,SALARY,,50000.00,149840.00\n"
    )
    parsed = parse_statement(statement)
    assert parsed.bank_slug == "hdfc"
    assert parsed.period_start.isoformat() == "2024-04-01"
    assert parsed.closing_balance == Decimal("149840.00")
    assert [row.amount for row in parsed.transactions] == [
        Decimal("-160.00"),
        Decimal("50000.00"),
    ]


def test_unsupported_file_has_actionable_error(tmp_path):
    statement = tmp_path / "statement.xlsx"
    statement.write_bytes(b"not a spreadsheet")
    with pytest.raises(ParseError, match="PDF, CSV, or text"):
        parse_statement(statement)
