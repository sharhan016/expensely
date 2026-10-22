import re
from dataclasses import dataclass

_UPI_HANDLE = re.compile(r"(?<![A-Z0-9._-])([A-Z0-9][A-Z0-9._-]{1,}@[A-Z][A-Z0-9.-]{1,})", re.I)
_DISPLAY_SEPARATORS = re.compile(r"[._-]+")


@dataclass(frozen=True)
class CounterpartyCandidate:
    display_name: str
    alias_type: str
    alias_value: str
    confidence: str


def extract_counterparty(description: str) -> CounterpartyCandidate | None:
    """Extract only identifiers stable enough to group without merging ledger rows."""
    match = _UPI_HANDLE.search(description)
    if not match:
        return None
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
