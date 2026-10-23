import re
from dataclasses import dataclass

_WRAPPED_HANDLE = re.compile(r"([A-Za-z0-9])-\s+(?=[A-Za-z0-9._-]*@)")
_UPI_HANDLE = re.compile(r"(?<![A-Z0-9._-])([A-Z0-9][A-Z0-9._-]{1,}@[A-Z][A-Z0-9.-]{1,})", re.I)
_DISPLAY_SEPARATORS = re.compile(r"[._-]+")
_TRAILING_DIGITS = re.compile(r"[\d\s]+$")
# The short name a bank prints right after the reference: "UPI/DR/123456789012/NOUSHATH/IBKL/..."
_BANK_NAME_SEGMENT = re.compile(r"^UPI(?:OUT|\s*IN)?[/\s]+(?:(?:DR|CR)/)?\d{6,}/([^/@]+)", re.I)
_NAME_LETTERS = 5
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


def _letters(text: str) -> int:
    return sum(1 for character in text if character.isalpha())


def _bank_name(description: str) -> str:
    """The name the bank printed for the other side, when it is plain letters."""
    match = _BANK_NAME_SEGMENT.match(description.strip())
    if not match:
        return ""
    words = match.group(1).split()
    if description.strip()[match.end(1) :].startswith("@"):
        words = words[:-1]  # the last word is the handle's local part, not a name
    name = " ".join(words)
    if not name.replace(" ", "").isalpha() or _letters(name) < 3:
        return ""
    return name.title()


def _handle_display(local_part: str, description: str, handle: str) -> str:
    """A person-readable label for a UPI handle; grouping still uses the handle itself.

    Trailing digits are noise ("nidnam2813-4" is "Nidnam"). When the handle holds too few letters
    to read as a name (a phone number, "mab.0371234"), the bank's own printed name is better.
    """
    readable = " ".join(_DISPLAY_SEPARATORS.sub(" ", local_part).split())
    name = _TRAILING_DIGITS.sub("", readable).title()
    if _letters(name) >= _NAME_LETTERS:
        return name
    return _bank_name(description) or (name if _letters(name) >= 4 else handle)


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
    # A PDF line wrap can leave a space inside a handle ("name1234- 1@bank"); rejoin it first.
    description = _WRAPPED_HANDLE.sub(r"\1-", description)
    match = _UPI_HANDLE.search(description)
    if match:
        handle = match.group(1).casefold()
        local_part = handle.split("@", 1)[0]
        display_name = _handle_display(local_part, description, handle)
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
