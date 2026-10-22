import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Transaction

PAYEE = "ZEBRAQUOKKA PAYEE"
JAN = (date(2026, 1, 1), date(2026, 1, 31))
APR = (date(2026, 4, 5), date(2026, 4, 30))
FACT_KINDS = {"exact", "possible", "fact", "gap", "close", "none"}
FREE_KINDS = {"note", "question", "error"}


def dataset(workspace, hint="1111"):
    """January: an exact 1200 out, two ATM withdrawals that add up to 1200, a close 1195."""
    account = workspace.account(hint)
    statement = workspace.stage(
        [
            (date(2026, 1, 5), f"UPI {PAYEE}", "-1200.00", ""),
            (date(2026, 1, 10), "ATM WDL SYNTH", "-500.00", ""),
            (date(2026, 1, 12), "ATM WDL SYNTH", "-700.00", ""),
            (date(2026, 1, 15), "UPI SYNTH CLOSE", "-1195.00", ""),
            (date(2026, 1, 20), "SYNTH SALARY", "5000.00", ""),
        ],
        hint=hint,
        opening="10000",
        closing="11405",
        period=JAN,
    )
    assert workspace.confirm(statement, account).status_code == 200
    workspace.client.get("/cash")
    return account


def ask(workspace, message):
    response = workspace.client.post("/investigate", json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()


def texts(answer):
    return [claim["text"] for claim in answer["claims"]]


def tool(workspace, name, body):
    return workspace.client.post(f"/investigate/tools/{name}", json=body)


def ids(workspace):
    rows = workspace.db.execute(
        select(Transaction.id, Transaction.amount, Transaction.txn_date)
    ).all()
    return {(str(r.amount), r.txn_date.isoformat()): r.id for r in rows}


# ---- golden answers ------------------------------------------------------------------------------


def test_golden_exact_match_and_cash_combination(workspace):
    dataset(workspace)
    by = ids(workspace)

    answer = ask(workspace, "where did my 1200 go")

    assert texts(answer) == [
        "Looking for ₹1,200.00 between 5 Jan 2026 and 20 Jan 2026.",
        "I found 1 movement of exactly ₹1,200.00: money out on 5 Jan 2026.",
        "Possible, not a cause: ATM withdrawals of ₹500.00 on 10 Jan 2026 and ₹700.00 on "
        "12 Jan 2026 add up to ₹1,200.00.",
        "A match is a record, not an explanation. I cannot tell from the records why money moved.",
    ]
    kinds = [c["kind"] for c in answer["claims"]]
    assert kinds == ["note", "exact", "possible", "note"]
    assert answer["claims"][1]["evidence"] == [f"txn:{by[('-1200.00', '2026-01-05')]}"]
    assert answer["claims"][2]["evidence"] == [
        f"txn:{by[('-500.00', '2026-01-10')]}",
        f"txn:{by[('-700.00', '2026-01-12')]}",
    ]
    assert answer["question"] is None
    assert "close" not in answer["content"].lower()  # near matches are off by default


def test_close_matches_are_labeled_and_only_shown_on_request(workspace):
    dataset(workspace)

    answer = ask(workspace, "where did my 1200 go, include close matches")

    close = [c for c in answer["claims"] if c["kind"] == "close"]
    assert [c["text"] for c in close] == [
        "Close, not exact: money out of ₹1,195.00 on 15 Jan 2026."
    ]
    assert close[0]["evidence"] and close[0]["evidence"][0].startswith("txn:")


def test_golden_balance_discontinuity_equal_to_the_amount_is_a_fact_not_a_cause(workspace):
    account = workspace.account()
    jan = workspace.stage(
        [(date(2026, 1, 5), "SYNTH A", "-100.00", "")], opening="1000", closing="900", period=JAN
    )
    workspace.confirm(jan, account)
    apr = workspace.stage(
        [(date(2026, 4, 6), "SYNTH B", "-50.00", "")], opening="2100", closing="2050", period=APR
    )
    workspace.confirm(apr, account)

    answer = ask(workspace, "where did my 1200 go")

    assert texts(answer) == [
        "Looking for ₹1,200.00 between 5 Jan 2026 and 6 Apr 2026.",
        "The statements do not connect by exactly ₹1,200.00 between 31 Jan 2026 and 5 Apr 2026. "
        "That is a fact about the records, not a cause.",
        "Nothing covers 1 Feb 2026 to 4 Apr 2026, so I cannot see what happened on those dates.",
        "A match is a record, not an explanation. I cannot tell from the records why money moved.",
    ]
    fact = answer["claims"][1]
    assert fact["kind"] == "fact" and any(r.startswith("disc:") for r in fact["evidence"])
    assert answer["question"] is None  # something concrete was found, no extra question


def test_golden_coverage_gap_with_no_matching_amount_asks_one_focused_question(workspace):
    account = workspace.account()
    workspace.confirm(
        workspace.stage(
            [(date(2026, 1, 5), "SYNTH A", "-100.00", "")],
            opening="1000",
            closing="900",
            period=JAN,
        ),
        account,
    )
    workspace.confirm(
        workspace.stage(
            [(date(2026, 4, 6), "SYNTH B", "-50.00", "")],
            opening="2100",
            closing="2050",
            period=APR,
        ),
        account,
    )

    answer = ask(workspace, "where did my 777 go")

    assert texts(answer) == [
        "Looking for ₹777.00 between 5 Jan 2026 and 6 Apr 2026.",
        "The balances also do not connect between 31 Jan 2026 and 5 Apr 2026 "
        "(difference ₹1,200.00).",
        "Nothing covers 1 Feb 2026 to 4 Apr 2026, so I cannot see what happened on those dates.",
        "I found nothing in my records that matches ₹777.00.",
        "Roughly when did it happen? Even a month helps.",
    ]
    assert answer["question"] == "Roughly when did it happen? Even a month helps."
    none = next(c for c in answer["claims"] if c["kind"] == "none")
    assert none["evidence"] == ["scope:2026-01-05:2026-04-06"]


def test_golden_nothing_found_says_so_and_asks_one_question(workspace):
    dataset(workspace)

    answer = ask(workspace, "where did my 999 go")

    assert texts(answer) == [
        "Looking for ₹999.00 between 5 Jan 2026 and 20 Jan 2026.",
        "I found nothing in my records that matches ₹999.00.",
        "5 movements in this window still have no purpose, so I cannot say what they were for.",
        "Roughly when did it happen? Even a month helps.",
    ]
    assert [c["kind"] for c in answer["claims"]] == ["note", "none", "fact", "question"]


def test_an_explicit_window_asks_which_account_instead_of_when(workspace):
    dataset(workspace)

    answer = ask(workspace, "where did my 999 go between 1 January 2026 and 31 January 2026")

    assert answer["question"] == "Which account was it on?"
    assert answer["claims"][0]["text"] == "Looking for ₹999.00 between 1 Jan 2026 and 31 Jan 2026."


def test_golden_a_failing_tool_is_reported_and_nothing_is_concluded_from_it(workspace, monkeypatch):
    dataset(workspace)
    from app.investigate import tools

    def broken(*args, **kwargs):
        raise RuntimeError(f"boom with {PAYEE} 4321.55")

    monkeypatch.setitem(
        tools.TOOLS, "find_cash_withdrawals", (tools.TOOLS["find_cash_withdrawals"][0], broken)
    )

    answer = ask(workspace, "where did my 1200 go")

    assert texts(answer) == [
        "Looking for ₹1,200.00 between 5 Jan 2026 and 20 Jan 2026.",
        "I found 1 movement of exactly ₹1,200.00: money out on 5 Jan 2026.",
        "I could not run one of the checks (cash withdrawals). I have not drawn any conclusion "
        "from it.",
        "A match is a record, not an explanation. I cannot tell from the records why money moved.",
    ]
    assert "boom" not in json.dumps(answer) and PAYEE not in json.dumps(answer)


def test_no_amount_asks_for_one_and_never_guesses(workspace):
    dataset(workspace)

    answer = ask(workspace, "where did my money go")

    assert answer["amount"] is None
    assert (
        answer["question"] == "Which amount are you looking for? For example: where did my 1200 go."
    )
    assert answer["claims"] == [] or all(c["kind"] in FREE_KINDS for c in answer["claims"])


def test_more_than_two_decimals_is_not_silently_rounded(workspace):
    dataset(workspace)

    answer = ask(workspace, "where did my 12.345 go")

    assert (
        answer["question"] == "Which amount are you looking for? For example: where did my 1200 go."
    )


# ---- evidence rule -------------------------------------------------------------------------------


def _check_evidence(workspace, answer):
    for claim in answer["claims"]:
        if claim["kind"] in FACT_KINDS:
            assert claim["evidence"], f"claim without evidence: {claim['text']}"
        else:
            assert claim["kind"] in FREE_KINDS and not any(ch.isdigit() for ch in "")
        for ref in claim["evidence"]:
            assert any(item["ref"] == ref for item in answer["evidence"]), ref
    refs = [item["ref"] for item in answer["evidence"]]
    if refs:
        assert (
            workspace.client.post("/investigate/evidence", json={"refs": refs}).status_code == 200
        )


@pytest.mark.parametrize(
    "message",
    [
        "where did my 1200 go",
        "where did my 999 go",
        "where did my 500 go",
        "where did my 700 go",
        "where did my 5000 go",
        "I cannot find 1195, include close matches",
        "where did my 1,200.00 go",
        "where did my ₹1200 go in January 2026 to 31 January 2026",
        "missing 12 rupees",
    ],
)
def test_no_answer_contains_a_claim_without_evidence_ids(workspace, message):
    dataset(workspace)

    answer = ask(workspace, message)

    _check_evidence(workspace, answer)
    assert (
        "because" not in answer["content"].lower() and "caused by" not in answer["content"].lower()
    )


def test_evidence_endpoint_returns_the_owned_rows_and_404s_on_anything_else(workspace):
    dataset(workspace)
    answer = ask(workspace, "where did my 1200 go")
    refs = [item["ref"] for item in answer["evidence"] if item["ref"].startswith("txn:")]

    rows = workspace.client.post("/investigate/evidence", json={"refs": refs}).json()

    assert [r["id"] for r in rows["transactions"]] == [int(r.split(":")[1]) for r in refs]
    assert Decimal(rows["transactions"][0]["amount"]) == Decimal("-1200.00")
    assert (
        workspace.client.post("/investigate/evidence", json={"refs": ["txn:999999"]}).status_code
        == 404
    )
    assert (
        workspace.client.post("/investigate/evidence", json={"refs": ["bogus"]}).status_code == 422
    )


# ---- tools: validation, ownership, privacy -------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("find_by_amount", {"amount": "12.345"}),
        ("find_by_amount", {"amount": "0"}),
        ("find_by_amount", {"amount": "-5"}),
        ("find_by_amount", {"amount": "1000000000000"}),
        ("find_by_amount", {"amount": "abc"}),
        ("find_by_amount", {"amount": "10", "date_from": "2026-03-01", "date_to": "2026-01-01"}),
        ("find_by_amount", {"amount": "10", "date_from": "2020-01-01", "date_to": "2026-01-01"}),
        ("find_by_amount", {"amount": "10", "limit": 501}),
        ("find_cash_withdrawals", {"amount": "12.345"}),
        (
            "find_cash_withdrawals",
            {"amount": "10", "date_from": "2020-01-01", "date_to": "2026-01-01"},
        ),
        ("related_links", {"transaction_id": "x"}),
        ("related_links", {"transaction_id": -1}),
        ("unresolved_in_window", {"date_from": "2020-01-01", "date_to": "2026-01-01"}),
        ("unresolved_in_window", {"date_from": "2026-01-01", "date_to": "2026-01-31", "limit": 0}),
        ("coverage_gaps", {"date_from": "2026-02-01", "date_to": "2026-01-01"}),
        ("balance_discontinuities", {"date_from": "2020-01-01", "date_to": "2026-01-01"}),
        (
            "balance_discontinuities",
            {"date_from": "2026-01-01", "date_to": "2026-01-31", "amount": "1.234"},
        ),
    ],
)
def test_every_tool_rejects_invalid_input(workspace, name, body):
    assert tool(workspace, name, body).status_code == 422
    assert workspace.client.post("/investigate/tools/nope", json={}).status_code == 404


def test_tools_return_only_owned_evidence(workspace):
    dataset(workspace)
    foreign_account = workspace.account("9999", user=workspace.stranger)
    foreign = Transaction(
        user_id=workspace.stranger.id,
        account_id=foreign_account.id,
        txn_date=date(2026, 1, 6),
        description="STRANGER",
        amount=Decimal("-1200.00"),
        dedupe_hash="foreign-1200",
    )
    workspace.db.add(foreign)
    workspace.db.commit()

    found = tool(workspace, "find_by_amount", {"amount": "1200"}).json()

    assert [m["transaction_id"] for m in found["matches"]] != []
    assert foreign.id not in [m["transaction_id"] for m in found["matches"]]
    assert tool(workspace, "related_links", {"transaction_id": foreign.id}).status_code == 404
    assert tool(workspace, "related_links", {"transaction_id": 999999}).status_code == 404
    assert (
        workspace.client.post(
            "/investigate/evidence", json={"refs": [f"txn:{foreign.id}"]}
        ).status_code
        == 404
    )
    assert workspace.db.scalar(select(func.count(Transaction.id))) == 6


def test_cash_combinations_are_at_most_three_and_always_labeled_possible(workspace):
    account = workspace.account()
    amounts = ["-100.00", "-200.00", "-300.00", "-400.00", "-250.00"]
    statement = workspace.stage(
        [(date(2026, 1, 2 + i), "ATM WDL SYNTH", a, "") for i, a in enumerate(amounts)],
        opening="5000",
        closing="3750",
        period=JAN,
    )
    workspace.confirm(statement, account)
    workspace.client.get("/cash")

    result = tool(workspace, "find_cash_withdrawals", {"amount": "600"}).json()

    sizes = sorted(len(c["transaction_ids"]) for c in result["combinations"])
    assert sizes and max(sizes) <= 3
    assert all(c["label"] == "possible" for c in result["combinations"])
    assert all(Decimal(c["total"]) == Decimal("600.00") for c in result["combinations"])


def test_related_links_and_unresolved_and_discontinuity_tools(workspace):
    dataset(workspace)
    by = ids(workspace)
    links = tool(
        workspace, "related_links", {"transaction_id": by[("-500.00", "2026-01-10")]}
    ).json()
    unresolved = tool(
        workspace, "unresolved_in_window", {"date_from": "2026-01-01", "date_to": "2026-01-31"}
    ).json()
    gaps = tool(
        workspace, "coverage_gaps", {"date_from": "2026-01-01", "date_to": "2026-02-10"}
    ).json()
    disc = tool(
        workspace, "balance_discontinuities", {"date_from": "2026-01-01", "date_to": "2026-01-31"}
    ).json()

    assert links["cash_withdrawal"] is True and links["links"] == []
    assert unresolved["total"] == 5 and len(unresolved["items"]) == 5
    assert [g["start"] for g in gaps["gaps"]] == ["2026-02-01"]
    assert disc["discontinuities"] == []


def test_tool_inputs_outputs_answers_and_logs_carry_no_descriptions_or_names(
    workspace, privacy_probe
):
    dataset(workspace)
    calls = [
        ("find_by_amount", {"amount": "1200"}),
        ("find_cash_withdrawals", {"amount": "1200"}),
        ("unresolved_in_window", {"date_from": "2026-01-01", "date_to": "2026-01-31"}),
        ("coverage_gaps", {"date_from": "2026-01-01", "date_to": "2026-03-31"}),
        ("balance_discontinuities", {"date_from": "2026-01-01", "date_to": "2026-03-31"}),
    ]
    for name, body in calls:
        privacy_probe.record(f"tool:{name}:in", body)
        privacy_probe.record(f"tool:{name}:out", tool(workspace, name, body).json())
    answer = ask(workspace, "where did my 1200 go")
    privacy_probe.record("answer", answer)
    tool(workspace, "find_by_amount", {"amount": "12.345"})  # a rejected call is logged safely too

    privacy_probe.assert_clean(PAYEE, "ZEBRAQUOKKA", "SYNTH SALARY")


def test_assistant_answers_where_did_my_amount_go_with_an_answer_card(workspace):
    dataset(workspace)

    reply = workspace.client.post(
        "/assistant/turn", json={"message": "where did my 1200 go"}
    ).json()

    assert reply["context"]["kind"] == "investigation"
    assert reply["context"]["claims"][1]["kind"] == "exact"
    assert reply["context"]["evidence"] and "exactly ₹1,200.00" in reply["content"]
    other = workspace.client.post(
        "/assistant/turn", json={"message": "5 October 2026 to 20 October 2026"}
    ).json()
    assert other["context"]["kind"] == "period_report"  # date ranges still go to the report
