from datetime import date
from decimal import Decimal

from test_money_in_motion import counterparty, move

from app.models import Purpose


def pass_through(workspace, in_day=date(2026, 8, 4), out_day=date(2026, 8, 4), amount="49941"):
    account = workspace.account()
    inn = move(workspace, account, in_day, amount, description="SYNTH IMPS-IN FRIEND")
    out = move(workspace, account, out_day, f"-{amount}", description="SYNTH UPI SHOP")
    return account, inn, out


def proposals(workspace):
    return workspace.client.get("/motion").json()["pass_through_proposals"]


def confirm(workspace, inn, out, path="/motion/pass-through"):
    return workspace.client.post(path, json={"outflow_id": out.id, "inflow_id": inn.id})


def test_same_amount_in_then_out_within_a_day_is_proposed_not_linked(workspace):
    _, inn, out = pass_through(workspace)

    found = proposals(workspace)

    assert [(p["inflow_id"], p["outflow_id"], p["amount"]) for p in found] == [
        (inn.id, out.id, "49941.00")
    ]
    report = workspace.client.get("/reports/period?from=2026-08-01&to=2026-08-31").json()
    assert report["buckets"]["passing_through"]["count"] == 0
    assert report["buckets"]["unexplained"]["count"] == 2


def test_confirming_makes_both_movements_neutral_and_stops_the_expect_back_question(workspace):
    friend = counterparty(workspace)
    account = workspace.account()
    inn = move(workspace, account, date(2026, 8, 4), "500", description="SYNTH IN")
    out = move(workspace, account, date(2026, 8, 4), "-500", cp=friend, description="SYNTH OUT")
    assert workspace.client.get("/motion").json()["questions"]

    assert confirm(workspace, inn, out).status_code == 200

    state = workspace.client.get("/motion").json()
    assert state["questions"] == [] and state["pass_through_proposals"] == []
    report = workspace.client.get("/reports/period?from=2026-08-01&to=2026-08-31").json()
    assert report["buckets"]["passing_through"]["count"] == 2
    assert report["buckets"]["unexplained"]["count"] == 0
    assert report["labels"]["passing_through"] == "Passed through your account"


def test_rejecting_hides_the_pair_and_undoes_a_confirmation(workspace):
    _, inn, out = pass_through(workspace)
    assert confirm(workspace, inn, out).status_code == 200

    assert confirm(workspace, inn, out, "/motion/pass-through/reject").status_code == 200

    assert proposals(workspace) == []
    report = workspace.client.get("/reports/period?from=2026-08-01&to=2026-08-31").json()
    assert report["buckets"]["passing_through"]["count"] == 0


def test_only_a_unique_same_account_pair_within_a_day_is_proposed(workspace):
    account = workspace.account()
    other = workspace.account(hint="2222")
    move(workspace, account, date(2026, 8, 4), "100", description="SYNTH IN A")
    move(workspace, other, date(2026, 8, 4), "-100", description="SYNTH OUT OTHER ACCOUNT")
    move(workspace, account, date(2026, 8, 4), "200", description="SYNTH IN B")
    move(workspace, account, date(2026, 8, 7), "-200", description="SYNTH OUT 3 DAYS LATER")
    move(workspace, account, date(2026, 8, 9), "300", description="SYNTH IN C")
    move(workspace, account, date(2026, 8, 9), "-300", description="SYNTH OUT C1")
    move(workspace, account, date(2026, 8, 9), "-300", description="SYNTH OUT C2")
    move(workspace, account, date(2026, 8, 10), "-50", description="SYNTH OUT BEFORE IN")
    move(workspace, account, date(2026, 8, 11), "50", description="SYNTH IN AFTER OUT")

    assert proposals(workspace) == []


def test_a_pass_through_needs_one_account_and_equal_opposite_amounts(workspace):
    account = workspace.account()
    other = workspace.account(hint="2222")
    inn = move(workspace, account, date(2026, 8, 4), "100", description="SYNTH IN")
    wrong_account = move(workspace, other, date(2026, 8, 4), "-100", description="SYNTH OUT")
    wrong_amount = move(workspace, account, date(2026, 8, 4), "-99", description="SYNTH OUT 2")

    assert confirm(workspace, inn, wrong_account).status_code == 409
    assert confirm(workspace, inn, wrong_amount).status_code == 409


def test_other_users_movements_cannot_be_linked(workspace):
    theirs = workspace.account(user=workspace.stranger)
    inn = move(workspace, theirs, date(2026, 8, 4), "100", user=workspace.stranger)
    out = move(workspace, theirs, date(2026, 8, 4), "-100", user=workspace.stranger)

    assert confirm(workspace, inn, out).status_code == 404


def test_amount_stays_decimal(workspace):
    _, inn, out = pass_through(workspace, amount="1234.56")
    assert Decimal(proposals(workspace)[0]["amount"]) == Decimal("1234.56")
    assert out.purpose == Purpose.OTHER and inn.purpose == Purpose.OTHER
