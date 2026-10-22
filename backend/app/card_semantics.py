import re
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CardSemantic:
    category: str
    confidence: str
    evidence: dict[str, str]


_SIGNALS = (
    (
        "card_payment",
        re.compile(r"\b(?:PAYMENT\s+RECEIVED|PAYMENT\s+THANK\s+YOU|AUTOPAY\s+PAYMENT)\b", re.I),
    ),
    ("reversal", re.compile(r"\b(?:REVERSAL|REVERSED|VOID)\b", re.I)),
    ("refund", re.compile(r"\b(?:REFUND|CREDIT\s+VOUCHER|MERCHANT\s+CREDIT)\b", re.I)),
    (
        "emi_interest",
        re.compile(
            r"\b(?:EMI|INSTAL+MENT).*\bINTEREST\b|\bINTEREST.*\b(?:EMI|INSTAL+MENT)\b", re.I
        ),
    ),
    ("interest", re.compile(r"\b(?:INTEREST|FINANCE\s+CHARGE)\b", re.I)),
    ("tax", re.compile(r"\b(?:GST|IGST|CGST|SGST|SERVICE\s+TAX)\b", re.I)),
    ("cash_advance", re.compile(r"\b(?:CASH\s+ADVANCE|ATM\s+CASH)\b", re.I)),
    ("emi_principal", re.compile(r"\b(?:EMI|INSTAL+MENT)\b", re.I)),
    ("foreign_currency_adjustment", re.compile(r"\b(?:FOREX|FOREIGN\s+CURRENCY|MARKUP)\b", re.I)),
    (
        "fee",
        re.compile(r"\b(?:ANNUAL|LATE|PROCESSING|OVERLIMIT|CASH\s+ADVANCE)?\s*FEE(?:S)?\b", re.I),
    ),
)


def classify_card_transaction(description: str, amount: Decimal) -> CardSemantic:
    for category, pattern in _SIGNALS:
        if pattern.search(description):
            if category in {"card_payment", "refund", "reversal"} and amount <= 0:
                continue
            return CardSemantic(
                category=category,
                confidence="high",
                evidence={"source": "yes_card_description", "signal": category},
            )
    if amount < 0:
        return CardSemantic(
            category="purchase",
            confidence="suggested",
            evidence={"source": "card_debit_direction", "signal": "debit"},
        )
    return CardSemantic(
        category="uncategorized",
        confidence="unresolved",
        evidence={"source": "card_credit_direction", "signal": "credit"},
    )
