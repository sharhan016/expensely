from decimal import Decimal

import pytest

from app.parsers.normalise import parse_amount, parse_date, parse_signed_amount
from app.parsers.statement import ParseError, _parse_yes_credit_card, parse_statement


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


def test_federal_statement_branding_detects_bank_and_account(tmp_path):
    statement = tmp_path / "federal.csv"
    statement.write_text(
        "Federal statement,Website:www.federalbank.co.in,,\n"
        "Account Number : 1088XXXXXX6482,,,\n"
        "Date,Narration,Withdrawal,Deposit,Closing Balance\n"
        "01/04/2024,UPI WATER,160.00,,99840.00\n"
    )

    parsed = parse_statement(statement)

    assert parsed.bank_slug == "federal"
    assert parsed.account_name == "Federal Bank Account"
    assert parsed.source_hint.endswith("6482")


def test_unsupported_file_has_actionable_error(tmp_path):
    statement = tmp_path / "statement.xlsx"
    statement.write_bytes(b"not a spreadsheet")
    with pytest.raises(ParseError, match="PDF, CSV, or text"):
        parse_statement(statement)


def test_yes_credit_card_text_normalises_spends_and_payments():
    text = """Credit Card Statement YES BANK
YES BANK Uni RuPay
Statement for YES BANK Card Number XXXX XXXX XXXX 9405
Statement Period: 17/09/2026 To 16/10/2026
Previous Balance : Rs. 19,329.72 Dr
Total Amount Due: Rs. 10,410.16
Statement Details
Date Transaction Details Merchant Category Amount (Rs.)
17/09/2026 UPI MARKET - Ref No: AB123 Groceries 1,000.00 Dr
18/09/2026 PAYMENT RECEIVED - Ref No: CD456 Payments 19,380.72 Cr
"""
    parsed = _parse_yes_credit_card(text)
    assert parsed.statement_kind == "credit_card"
    assert parsed.account_name == "YES BANK Uni RuPay Credit Card"
    assert parsed.source_hint.endswith("9405")
    assert parsed.instrument_confidence == "high"
    assert parsed.parser_name == "yes_credit_card"
    assert parsed.opening_balance == Decimal("-19329.72")
    assert parsed.closing_balance == Decimal("-10410.16")
    assert [row.amount for row in parsed.transactions] == [
        Decimal("-1000.00"),
        Decimal("19380.72"),
    ]


@pytest.mark.parametrize(
    ("account_type", "expected_kind", "expected_name"),
    [
        ("Current", "current", "YES BANK Current Account"),
        ("Savings", "savings", "YES BANK Savings Account"),
    ],
)
def test_yes_deposit_statement_detects_instrument_from_document_evidence(
    tmp_path, account_type, expected_kind, expected_name
):
    statement = tmp_path / "yes.csv"
    statement.write_text(
        "YES BANK Statement,,,,\n"
        f"Account Type: {account_type},,,,\n"
        "Account Number: 0012XXXX9405,,,,\n"
        "Date,Narration,Withdrawal,Deposit,Closing Balance\n"
        "01/04/2026,UPI WATER,160.00,,99840.00\n"
    )

    parsed = parse_statement(statement)

    assert parsed.bank_slug == "yes"
    assert parsed.statement_kind == expected_kind
    assert parsed.account_name == expected_name
    assert parsed.source_hint.endswith("9405")
    assert parsed.instrument_confidence == "high"
    assert parsed.parser_name == "yes_deposit_table"


def test_yes_deposit_statement_does_not_guess_instrument_without_evidence(tmp_path):
    statement = tmp_path / "yes.csv"
    statement.write_text(
        "YES BANK Statement,,,,\n"
        "Account Number: 0012XXXX9405,,,,\n"
        "Date,Narration,Withdrawal,Deposit,Closing Balance\n"
        "01/04/2026,UPI WATER,160.00,,99840.00\n"
    )

    parsed = parse_statement(statement)

    assert parsed.statement_kind == "bank"
    assert parsed.instrument_confidence == "unknown"
