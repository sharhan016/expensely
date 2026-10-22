from datetime import date
from decimal import Decimal

from app.coverage import (
    AccountEntry,
    StatementSpan,
    compute_coverage,
    describe_account,
    describe_overall,
    overall_coverage,
)


def span(key, start, end, opening, closing, state="balanced"):
    return StatementSpan(
        key=key,
        start=start,
        end=end,
        opening=Decimal(opening) if opening is not None else None,
        closing=Decimal(closing) if closing is not None else None,
        state=state,
    )


JAN = span("jan", date(2026, 1, 1), date(2026, 1, 31), "1000", "900")
FEB = span("feb", date(2026, 2, 1), date(2026, 2, 28), "900", "800")
MAR = span("mar", date(2026, 3, 1), date(2026, 3, 31), "800", "700")


def kinds(coverage):
    return [segment.kind for segment in coverage.segments]


def test_two_connected_statements_are_reconciled_through_the_later_end():
    result = compute_coverage([JAN, FEB])

    assert result.reconciled_through == date(2026, 2, 28)
    assert kinds(result) == ["covered", "covered"]
    assert result.covered == [(date(2026, 1, 1), date(2026, 2, 28))]
    assert result.first_problem is None


def test_input_order_does_not_matter():
    assert compute_coverage([FEB, MAR, JAN]).reconciled_through == date(2026, 3, 31)


def test_gap_with_matching_balances_is_bridged_visibly_and_the_chain_advances():
    apr = span("apr", date(2026, 4, 5), date(2026, 4, 30), "700", "650")
    result = compute_coverage([JAN, FEB, MAR, apr])

    assert result.reconciled_through == date(2026, 4, 30)
    bridged = [segment for segment in result.segments if segment.kind == "bridged"]
    assert [(item.start, item.end) for item in bridged] == [(date(2026, 4, 1), date(2026, 4, 4))]
    # A bridged gap is never reported as plain covered.
    assert result.covered == [
        (date(2026, 1, 1), date(2026, 3, 31)),
        (date(2026, 4, 5), date(2026, 4, 30)),
    ]


def test_unbridging_a_gap_moves_reconciled_through_back():
    apr = span("apr", date(2026, 4, 5), date(2026, 4, 30), "700", "650")

    result = compute_coverage(
        [JAN, FEB, MAR, apr], unbridged=frozenset({(date(2026, 4, 1), date(2026, 4, 4))})
    )

    assert result.reconciled_through == date(2026, 3, 31)
    assert "gap" in kinds(result) and "bridged" not in kinds(result)
    assert result.first_problem.kind == "gap"


def test_gap_with_different_balances_is_a_discontinuity_and_stops_the_chain():
    apr = span("apr", date(2026, 4, 5), date(2026, 4, 30), "650", "600")
    result = compute_coverage([JAN, FEB, MAR, apr])

    assert result.reconciled_through == date(2026, 3, 31)
    problem = result.first_problem
    assert problem.kind == "discontinuity"
    assert (problem.start, problem.end) == (date(2026, 4, 1), date(2026, 4, 4))


def test_touching_statements_whose_balances_differ_are_a_discontinuity():
    feb = span("feb", date(2026, 2, 1), date(2026, 2, 28), "890", "800")
    result = compute_coverage([JAN, feb, MAR])

    assert result.reconciled_through == date(2026, 1, 31)
    assert result.first_problem.kind == "discontinuity"
    assert result.first_problem.start == date(2026, 2, 1)


def test_nothing_after_a_break_can_restore_the_chain():
    feb = span("feb", date(2026, 2, 1), date(2026, 2, 28), "890", "800")

    assert compute_coverage([JAN, feb, MAR]).reconciled_through == date(2026, 1, 31)


def test_gap_without_comparable_balances_is_a_plain_gap():
    feb = span("feb", date(2026, 2, 10), date(2026, 2, 28), None, None, "unverifiable")
    result = compute_coverage([JAN, feb])

    assert result.reconciled_through == date(2026, 1, 31)
    assert kinds(result) == ["covered", "gap", "unverifiable"]


def test_layout_without_balance_endpoints_is_covered_but_not_verifiable():
    feb = span("feb", date(2026, 2, 1), date(2026, 2, 28), None, None, "unverifiable")
    result = compute_coverage([JAN, feb, MAR])

    assert result.reconciled_through == date(2026, 1, 31)
    assert kinds(result)[:2] == ["covered", "unverifiable"]
    assert result.covered == [
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 3, 1), date(2026, 3, 31)),
    ]


def test_statement_with_a_difference_never_counts():
    feb = span("feb", date(2026, 2, 1), date(2026, 2, 28), "900", "800", "difference")

    result = compute_coverage([JAN, feb])

    assert result.reconciled_through == date(2026, 1, 31)
    assert result.first_problem.kind == "difference"


def test_invalidated_and_unverified_statements_do_not_count():
    for state in ("invalidated", "unverified"):
        feb = span("feb", date(2026, 2, 1), date(2026, 2, 28), "900", "800", state)
        result = compute_coverage([JAN, feb])
        assert result.reconciled_through == date(2026, 1, 31)
        assert result.first_problem.kind == state


def test_overlapping_reupload_adds_nothing_and_does_not_break_the_chain():
    again = span("jan-again", date(2026, 1, 10), date(2026, 1, 31), "950", "900")

    result = compute_coverage([JAN, again, FEB])

    assert result.reconciled_through == date(2026, 2, 28)
    assert len(result.segments) == 2


def test_partial_overlap_extends_coverage_only_by_the_new_days():
    feb = span("feb", date(2026, 1, 25), date(2026, 2, 28), "910", "800")

    result = compute_coverage([JAN, feb])

    assert result.reconciled_through == date(2026, 2, 28)
    assert (result.segments[1].start, result.segments[1].end) == (
        date(2026, 2, 1),
        date(2026, 2, 28),
    )


def test_records_start_anchors_the_chain_past_an_early_gap():
    early = span("early", date(2025, 6, 1), date(2025, 6, 30), "500", "480")

    unanchored = compute_coverage([early, JAN, FEB])
    anchored = compute_coverage([early, JAN, FEB], records_start=date(2026, 1, 1))

    assert unanchored.reconciled_through == date(2025, 6, 30)
    assert anchored.reconciled_through == date(2026, 2, 28)
    assert anchored.records_start == date(2026, 1, 1)


def test_records_start_after_first_statement_start_uses_opening_balance_anchor():
    result = compute_coverage([FEB], records_start=date(2026, 1, 20), anchor_opening=Decimal("900"))
    assert result.reconciled_through == date(2026, 2, 28)
    assert "bridged" in kinds(result)

    wrong = compute_coverage([FEB], records_start=date(2026, 1, 20), anchor_opening=Decimal("5"))
    assert wrong.reconciled_through is None
    assert wrong.first_problem.kind == "discontinuity"


def test_start_with_no_anchor_and_no_statement_for_it_is_a_gap():
    result = compute_coverage([FEB], records_start=date(2026, 1, 20))

    assert result.reconciled_through is None
    assert result.first_problem.kind == "gap"


def test_changing_records_start_changes_only_the_computation():
    spans = [JAN, FEB]
    before = compute_coverage(spans)
    compute_coverage(spans, records_start=date(2026, 2, 1))

    assert compute_coverage(spans) == before


def test_no_statements_means_nothing_is_reconciled():
    result = compute_coverage([])

    assert result.reconciled_through is None
    assert result.segments == []


def test_recompute_is_idempotent():
    spans = [JAN, FEB, MAR]
    assert compute_coverage(spans) == compute_coverage(list(reversed(spans)))


def _entry(account_id, name, through, in_scope=True):
    spans = [JAN, FEB] if through == "feb" else [JAN, FEB, MAR] if through == "mar" else []
    return AccountEntry(account_id, name, in_scope, compute_coverage(spans))


def test_overall_is_the_earliest_account_and_names_the_laggard():
    result = overall_coverage([_entry(1, "Account A", "mar"), _entry(2, "Account B", "feb")])

    assert result.reconciled_through == date(2026, 2, 28)
    assert result.lagging_account_id == 2
    assert result.lagging_name == "Account B"


def test_accounts_out_of_scope_are_ignored_by_the_overall_line():
    result = overall_coverage(
        [_entry(1, "Account A", "mar"), _entry(2, "Account B", "feb", in_scope=False)]
    )

    assert result.reconciled_through == date(2026, 3, 31)
    assert result.in_scope_count == 1


def test_account_with_no_reconciled_records_holds_the_overall_line_back():
    result = overall_coverage([_entry(1, "Account A", "mar"), _entry(2, "Account B", "none")])

    assert result.reconciled_through is None
    assert result.lagging_name == "Account B"


def test_plain_language_summaries_name_dates_and_problems():
    apr = span("apr", date(2026, 4, 5), date(2026, 4, 30), "650", "600")
    coverage = compute_coverage([JAN, FEB, MAR, apr])

    line = describe_account("Account A", coverage, today=date(2026, 5, 10))

    assert "31 Mar 2026" in line
    assert "1 Apr 2026 to 4 Apr 2026" in line
    assert "balances do not connect" in line
    overall = overall_coverage([AccountEntry(1, "Account A", True, coverage)])
    assert "31 Mar 2026" in describe_overall(overall, today=date(2026, 5, 10))
    assert "40 days" in describe_overall(overall, today=date(2026, 5, 10))
