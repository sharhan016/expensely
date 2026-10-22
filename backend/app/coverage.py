"""Per-account statement coverage and the "reconciled through" date.

Pure functions over plain dataclasses: no database, no clock. The chain starts at the account's
records start and runs through consecutive statements that are balanced, verifiable and not
invalidated, and whose balances connect. It stops at the first gap, discontinuity, unverifiable,
unreconciled or invalidated statement; nothing later can restore it.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

# Span states (how well one statement is verified) and segment kinds (what is true of a date range).
BALANCED = "balanced"
PROBLEM_STATES = ("difference", "unverifiable", "unverified", "invalidated", "period_mismatch")
NEUTRAL_KINDS = ("covered", "bridged")

DAY = timedelta(days=1)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def date_label(value: date) -> str:
    return f"{value.day} {MONTHS[value.month - 1]} {value.year}"


@dataclass(frozen=True)
class StatementSpan:
    key: str
    start: date
    end: date
    opening: Decimal | None
    closing: Decimal | None
    state: str  # balanced | difference | unverifiable | unverified | invalidated


@dataclass(frozen=True)
class Segment:
    kind: str  # covered | bridged | gap | discontinuity | difference | unverifiable | ...
    start: date
    end: date
    detail: str = ""
    key: str | None = None
    unbridged: bool = False


@dataclass(frozen=True)
class AccountCoverage:
    records_start: date | None
    reconciled_through: date | None
    segments: list[Segment]
    covered: list[tuple[date, date]]
    first_problem: Segment | None


def compute_coverage(
    spans: list[StatementSpan],
    *,
    records_start: date | None = None,
    anchor_opening: Decimal | None = None,
    unbridged: frozenset[tuple[date, date]] = frozenset(),
    acknowledged: frozenset[tuple[date, date]] = frozenset(),
) -> AccountCoverage:
    ordered = sorted(spans, key=lambda item: (item.start, item.end, item.key))
    start_date = records_start or (ordered[0].start if ordered else None)
    if start_date is None:
        return AccountCoverage(None, None, [], [], None)
    ordered = [item for item in ordered if item.end >= start_date]

    segments: list[Segment] = []
    cursor_end = start_date - DAY
    cursor_closing = anchor_opening
    chain_end: date | None = None
    broken = False

    for item in ordered:
        if item.end <= cursor_end:
            continue  # a re-upload of days already covered adds nothing and breaks nothing
        link_ok = True
        comparable = cursor_closing is not None and item.opening is not None
        if item.start > cursor_end + DAY:
            gap_start, gap_end = cursor_end + DAY, item.start - DAY
            if (gap_start, gap_end) in acknowledged:
                # She said she does not need these dates. They stay unchecked: never counted.
                segments.append(
                    Segment(
                        "acknowledged",
                        gap_start,
                        gap_end,
                        "You said you do not need these dates. They are not counted as checked.",
                    )
                )
                link_ok = False
            elif comparable and cursor_closing == item.opening:
                if (gap_start, gap_end) in unbridged:
                    segments.append(
                        Segment(
                            "gap", gap_start, gap_end, "You un-bridged this gap.", unbridged=True
                        )
                    )
                    link_ok = False
                else:
                    segments.append(
                        Segment(
                            "bridged",
                            gap_start,
                            gap_end,
                            "Balances connect, but there are no statement rows for these dates.",
                        )
                    )
            elif comparable:
                segments.append(
                    Segment(
                        "discontinuity",
                        gap_start,
                        gap_end,
                        "The balances do not connect across these dates.",
                    )
                )
                link_ok = False
            else:
                segments.append(
                    Segment("gap", gap_start, gap_end, "No statement covers these dates.")
                )
                link_ok = False
        elif item.start == cursor_end + DAY and comparable and cursor_closing != item.opening:
            segments.append(
                Segment(
                    "discontinuity",
                    item.start,
                    item.start,
                    "The balances do not connect at this date.",
                )
            )
            link_ok = False

        kind = "covered" if item.state == BALANCED else item.state
        segments.append(Segment(kind, max(item.start, cursor_end + DAY), item.end, key=item.key))
        if not broken and link_ok and item.state == BALANCED:
            chain_end = item.end
        else:
            broken = True
        cursor_end = item.end
        cursor_closing = item.closing

    covered: list[tuple[date, date]] = []
    for segment in segments:
        if segment.kind != "covered":
            continue
        if covered and covered[-1][1] + DAY == segment.start:
            covered[-1] = (covered[-1][0], segment.end)
        else:
            covered.append((segment.start, segment.end))
    first_problem = next((s for s in segments if s.kind not in NEUTRAL_KINDS), None)
    return AccountCoverage(start_date, chain_end, segments, covered, first_problem)


@dataclass(frozen=True)
class AccountEntry:
    account_id: int
    name: str
    in_scope: bool
    coverage: AccountCoverage


@dataclass(frozen=True)
class OverallCoverage:
    reconciled_through: date | None
    lagging_account_id: int | None
    lagging_name: str | None
    in_scope_count: int


def overall_coverage(entries: list[AccountEntry]) -> OverallCoverage:
    """Earliest reconciled-through date among in-scope accounts, naming the one holding it back."""
    scoped = [entry for entry in entries if entry.in_scope]
    if not scoped:
        return OverallCoverage(None, None, None, 0)
    laggard = min(
        scoped,
        key=lambda entry: (
            entry.coverage.reconciled_through is not None,
            entry.coverage.reconciled_through or date.min,
            entry.name,
        ),
    )
    return OverallCoverage(
        laggard.coverage.reconciled_through, laggard.account_id, laggard.name, len(scoped)
    )


_PROBLEM_TEXT = {
    "gap": "no statement covers {span}",
    "acknowledged": "you said {span} is not needed, so it is not counted as checked",
    "discontinuity": "the balances do not connect at {span}",
    "difference": "the statement for {span} does not add up",
    "unverifiable": "the statement for {span} has no opening or closing balance, so it cannot "
    "be checked",
    "unverified": "the check for {span} is out of date and needs a re-check",
    "invalidated": "a movement in {span} changed after it was checked",
    "period_mismatch": "the statement for {span} adds up, but the movements recorded for those "
    "dates do not add up with it",
}


def _span_text(segment: Segment) -> str:
    if segment.start == segment.end:
        return date_label(segment.start)
    return f"{date_label(segment.start)} to {date_label(segment.end)}"


def _problem_sentence(segment: Segment) -> str:
    template = _PROBLEM_TEXT.get(segment.kind, "{span} could not be checked")
    return template.format(span=_span_text(segment))


def describe_account(name: str, coverage: AccountCoverage, *, today: date) -> str:
    if coverage.reconciled_through is None:
        base = f"{name}: nothing is reconciled yet."
    else:
        age = (today - coverage.reconciled_through).days
        base = (
            f"{name}: reconciled through {date_label(coverage.reconciled_through)}"
            f" ({age} days ago)."
        )
    if coverage.first_problem is not None:
        base += f" Next problem: {_problem_sentence(coverage.first_problem)}."
    return base


def describe_overall(overall: OverallCoverage, *, today: date) -> str:
    if not overall.in_scope_count:
        return "No accounts are in scope, so there is nothing to reconcile."
    if overall.reconciled_through is None:
        return (
            f"Nothing is reconciled yet; {overall.lagging_name} has no checked statements."
            if overall.lagging_name
            else "Nothing is reconciled yet."
        )
    age = (today - overall.reconciled_through).days
    text = f"Everything in scope is reconciled through {date_label(overall.reconciled_through)}"
    text += f" ({age} days ago)."
    if overall.in_scope_count > 1 and overall.lagging_name:
        text += f" {overall.lagging_name} is the furthest behind."
    return text
