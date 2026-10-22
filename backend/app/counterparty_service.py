import re
from dataclasses import dataclass

_UPI_HANDLE = re.compile(r"(?<![A-Z0-9._-])([A-Z0-9][A-Z0-9._-]{1,}@[A-Z][A-Z0-9.-]{1,})", re.I)
_DISPLAY_SEPARATORS = re.compile(r"[._-]+")
_REFERENCE_FRAGMENT = re.compile(
    r"\s*(?:-|/)?\s*\b(?:REF(?:ERENCE)?(?:\s+NO)?|RRN|TXN(?:\s+ID)?)"
    r"\s*[:#/-]*\s*[A-Z0-9-]+\b.*$",
    re.I,
)
_MERCHANT_PREFIX = re.compile(
    r"^(?:(?:CARD\s+)?(?:PURCHASE|POS|ECOM(?:MERCE)?|ONLINE)\s*[-/:]*\s*)+",
    re.I,
)
_MERCHANT_EVENT = re.compile(r"\b(?:REFUND|REVERSAL|REVERSED)\b", re.I)
_TRAILING_CATEGORY = re.compile(
    r"\b(?:SHOPPING|GROCERIES|DINING|TRAVEL|FUEL|UTILITIES|ENTERTAINMENT|PAYMENTS?)\s*$",
    re.I,
)
_MERCHANT_SEPARATORS = re.compile(r"[^A-Z0-9]+", re.I)
_MERCHANT_CATEGORIES = {"purchase", "refund", "reversal"}


@dataclass(frozen=True)
class CounterpartyCandidate:
    display_name: str
    alias_type: str
    alias_value: str
    confidence: str


def extract_counterparty(
    description: str, category: str | None = None
) -> CounterpartyCandidate | None:
    """Extract only identifiers stable enough to group without merging ledger rows."""
    match = _UPI_HANDLE.search(description)
    if match:
        handle = match.group(1).casefold()
        local_part = handle.split("@", 1)[0]
        readable = " ".join(_DISPLAY_SEPARATORS.sub(" ", local_part).split()).title()
        display_name = readable if any(character.isalpha() for character in readable) else handle
        return CounterpartyCandidate(
            display_name=display_name,
            alias_type="upi_handle",
            alias_value=handle,
            confidence="high",
        )
    if category not in _MERCHANT_CATEGORIES:
        return None
    merchant = _REFERENCE_FRAGMENT.sub("", description)
    merchant = _MERCHANT_PREFIX.sub("", merchant)
    merchant = _MERCHANT_EVENT.sub("", merchant)
    merchant = _TRAILING_CATEGORY.sub("", merchant)
    merchant = " ".join(_MERCHANT_SEPARATORS.sub(" ", merchant).casefold().split())
    if len(merchant) < 3 or not any(character.isalpha() for character in merchant):
        return None
    return CounterpartyCandidate(
        display_name=merchant.title(),
        alias_type="merchant_token",
        alias_value=merchant,
        confidence="high",
    )
