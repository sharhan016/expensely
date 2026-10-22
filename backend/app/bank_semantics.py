"""Conservative built-in mapping for obvious bank-generated lines on account statements.

Only unmistakable bank wording is matched (charges, interest paid). The result is a suggestion for
her to confirm in the review table, never a confirmed decision, and it never touches a row that
already has a rule, a remembered counterparty or a history suggestion.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from app.models import Purpose

_CHARGE = re.compile(r"\b(?:CHRG|CHRGS|CHARGES?|ANNUAL\s+FEE|SERVICE\s+CHARGE|AMC)\b", re.I)
_INTEREST = re.compile(r"\bINT\.?\s*PD\b|\bINTEREST\s+(?:PAID|CREDIT\w*)\b|\bSBINT\b", re.I)


@dataclass(frozen=True)
class BankHint:
    category: str
    purpose: Purpose
    rule: str


def bank_line_hint(description: str, amount: Decimal) -> BankHint | None:
    if amount < 0 and _CHARGE.search(description):
        return BankHint("fee", Purpose.PERSONAL, "bank_charge")
    if amount > 0 and _INTEREST.search(description):
        return BankHint("interest", Purpose.INCOME, "bank_interest")
    return None
