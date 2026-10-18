from datetime import date
from decimal import Decimal

from app.models import Purpose
from app.services import calculate_reconciliation, dedupe_hash, suggest_from_history


def test_dedupe_hash_normalizes_case_and_spacing():
    first = dedupe_hash(1, date(2026, 10, 1), Decimal("-1250.00"), "UPI  ABC STORES", "ref-1")
    second = dedupe_hash(1, date(2026, 10, 1), Decimal("-1250"), " upi abc stores ", "REF-1")
    assert first == second


def test_dedupe_hash_keeps_distinct_references_separate():
    first = dedupe_hash(1, date(2026, 10, 1), Decimal("-500"), "UPI FATAH", "001")
    second = dedupe_hash(1, date(2026, 10, 1), Decimal("-500"), "UPI FATAH", "002")
    assert first != second


def test_reconciliation_balances_exactly_with_decimal_money():
    expected, difference = calculate_reconciliation(
        Decimal("42150"),
        [
            Decimal("60000"),
            Decimal("-3000"),
            Decimal("-4000"),
            Decimal("-1250"),
            Decimal("15000"),
            Decimal("-160"),
        ],
        Decimal("108740"),
    )
    assert expected == Decimal("108740")
    assert difference == Decimal("0")


def test_reconciliation_reports_difference_without_guessing_cause():
    expected, difference = calculate_reconciliation(
        Decimal("1000"), [Decimal("250"), Decimal("-100")], Decimal("1140")
    )
    assert expected == Decimal("1150")
    assert difference == Decimal("-10")


def test_previous_description_produces_reviewable_suggestion_without_amount():
    purpose, confidence = suggest_from_history(
        "UPI WATER CAN DELIVERY 999999999999",
        [("UPI WATER CAN DELIVERY 123456789012", Purpose.BUSINESS)],
    )
    assert purpose == Purpose.BUSINESS
    assert confidence == "confident"


def test_unrelated_description_remains_unresolved():
    purpose, confidence = suggest_from_history(
        "NEFT SCHOOL FEES", [("UPI WATER CAN DELIVERY", Purpose.BUSINESS)]
    )
    assert purpose is None
    assert confidence == "unresolved"
