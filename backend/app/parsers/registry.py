from collections.abc import Callable

from app.parsers.banks.yes import matches as matches_yes
from app.parsers.banks.yes import parse_statement as parse_yes
from app.parsers.types import ParsedStatement

StatementMatcher = Callable[[str], bool]
StatementParser = Callable[[str, list[list[str]]], ParsedStatement]

_BANK_PARSERS: tuple[tuple[StatementMatcher, StatementParser], ...] = ((matches_yes, parse_yes),)


def parse_known_statement(text: str, rows: list[list[str]]) -> ParsedStatement | None:
    for matches, parse in _BANK_PARSERS:
        if matches(text):
            return parse(text, rows)
    return None
