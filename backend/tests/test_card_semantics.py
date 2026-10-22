from decimal import Decimal

import pytest

from app.card_semantics import classify_card_transaction


@pytest.mark.parametrize(
    ("description", "amount", "category"),
    [
        ("ANNUAL FEE", "-500.00", "fee"),
        ("FINANCE CHARGE INTEREST", "-120.00", "interest"),
        ("GST ON FEE", "-90.00", "tax"),
        ("CASH ADVANCE ATM", "-1000.00", "cash_advance"),
        ("EMI INTEREST", "-75.00", "emi_interest"),
        ("EMI PRINCIPAL", "-800.00", "emi_principal"),
        ("FOREX MARKUP", "-33.00", "foreign_currency_adjustment"),
        ("AMAZON REFUND", "500.00", "refund"),
        ("AMAZON REVERSAL", "500.00", "reversal"),
        ("PAYMENT RECEIVED", "500.00", "card_payment"),
        ("LOCAL SHOP", "-500.00", "purchase"),
    ],
)
def test_card_semantics_keep_charges_and_credits_distinct(description, amount, category):
    semantic = classify_card_transaction(description, Decimal(amount))

    assert semantic.category == category
    assert semantic.evidence["source"] in {"yes_card_description", "card_debit_direction"}


def test_positive_unknown_card_credit_remains_unresolved():
    semantic = classify_card_transaction("MANUAL CREDIT", Decimal("100.00"))

    assert semantic.category == "uncategorized"
    assert semantic.confidence == "unresolved"
