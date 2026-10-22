from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from test_investigate import _check_evidence, ask, texts, tool
from test_money_in_motion import answer, buckets, counterparty, motion, move, report, three_payments

from app.models import (
    ExpectationAllocation,
    MoneyExpectation,
    MotionPreference,
    Person,
    Purpose,
    Transaction,
    TransactionLink,
    TransactionMeaning,
    TransactionMeaningChange,
)

ASHA = "Asha Synthetic"


def set_status(workspace, expectation_id, status):
    return workspace.client.post(
        f"/motion/expectations/{expectation_id}/status", json={"status": status}
    )


def expectation_id(workspace):
    return motion(workspace)["outstanding"][0]["expectation_id"]


# ---- (2) written off


def test_written_off_items_leave_in_motion_and_get_their_own_line_everywhere(workspace):
    asha = counterparty(workspace)
    three_payments(workspace, asha)
    answer(workspace, asha, "expected_back")

    set_status(workspace, expectation_id(workspace), "written_off")

    b = buckets(workspace)
    assert (b["written_off"]["count"], b["written_off"]["out"]) == (3, "3000.00")
    assert b["in_motion"]["count"] == 0 and b["spending"]["count"] == 0
    body = report(workspace)
    assert body["labels"]["written_off"] == "Written off (not counted)"
    assert "Written off (not counted): ₹3,000.00" in body["summary"]
    dashboard = workspace.client.get(
        "/dashboard", params={"from": "2026-01-01", "to": "2026-03-31"}
    ).json()
    assert dashboard["period"]["buckets"] == body["buckets"]
    reply = workspace.client.post("/assistant/turn", json={"message": "what is still out?"}).json()
    assert "Written off (not counted)" in reply["content"] and "₹3,000.00" in reply["content"]


def test_undoing_a_write_off_restores_the_expectation(workspace):
    asha = counterparty(workspace)
    three_payments(workspace, asha)
    answer(workspace, asha, "expected_back")
    expectation = expectation_id(workspace)
    set_status(workspace, expectation, "written_off")

    set_status(workspace, expectation, "open")

    b = buckets(workspace)
    assert (b["in_motion"]["out"], b["written_off"]["count"]) == ("3000.00", 0)
    assert motion(workspace)["outstanding"][0]["remaining"] == "3000.00"


def test_not_expected_back_returns_the_payments_to_unknown_and_asks_again(workspace):
    asha = counterparty(workspace)
    three_payments(workspace, asha)
    answer(workspace, asha, "expected_back")

    set_status(workspace, expectation_id(workspace), "not_expected")

    b = buckets(workspace)
    assert (b["unexplained"]["out"], b["in_motion"]["count"], b["written_off"]["count"]) == (
        "3000.00",
        0,
        0,
    )
    (question,) = motion(workspace)["questions"]
    assert question["count"] == 3


# ---- (3) remember for this person


def test_remembering_is_off_by_default_and_applies_only_to_later_payments(workspace):
    asha = counterparty(workspace)
    account, rows = three_payments(workspace, asha)
    assert answer(workspace, asha, "expense", rows[:1]).status_code == 200
    assert workspace.db.scalar(select(func.count(MotionPreference.id))) == 0  # off unless asked

    workspace.client.post(
        "/motion/answers",
        json={
            "counterparty_id": asha.id,
            "answer": "expense",
            "transaction_ids": [rows[1].id],
            "remember": True,
        },
    )

    assert workspace.db.scalar(select(func.count(MotionPreference.id))) == 1
    # The third payment already existed: it stays a question, never auto-applied.
    (question,) = motion(workspace)["questions"]
    assert question["transaction_ids"] == [rows[2].id]
    # A later payment is applied automatically, disclosed, and audited.
    later = move(workspace, account, date(2026, 2, 3), "-250.00", asha, key="later")
    state = motion(workspace)
    workspace.db.expire_all()
    meaning = workspace.db.get(TransactionMeaning, later.id)
    assert (meaning.purpose, meaning.decision_source) == (Purpose.PERSONAL, "confirmed_rule")
    (item,) = state["remembered"]
    assert (item["counterparty_name"], item["choice"], item["applied_count"]) == (
        ASHA,
        "expense",
        1,
    )
    change = workspace.db.scalar(
        select(TransactionMeaningChange).where(TransactionMeaningChange.transaction_id == later.id)
    )
    assert "remembered choice" in change.reason
    assert buckets(workspace)["spending"]["count"] == 3  # two answered + one remembered


def test_remembered_choices_are_replay_safe_and_never_touch_answered_payments(workspace):
    asha = counterparty(workspace)
    account, rows = three_payments(workspace, asha)
    answer(workspace, asha, "expected_back", rows[:2])
    workspace.client.post(
        "/motion/answers",
        json={
            "counterparty_id": asha.id,
            "answer": "expense",
            "transaction_ids": [rows[2].id],
            "remember": True,
        },
    )
    workspace.client.post(
        "/motion/answers",
        json={
            "counterparty_id": asha.id,
            "answer": "expense",
            "transaction_ids": [rows[2].id],
            "remember": True,
        },
    )
    later = move(workspace, account, date(2026, 2, 3), "-250.00", asha, key="later")
    for _ in range(3):
        motion(workspace)

    assert workspace.db.scalar(select(func.count(MotionPreference.id))) == 1
    assert (
        workspace.db.scalar(
            select(func.count(TransactionMeaningChange.id)).where(
                TransactionMeaningChange.transaction_id == later.id
            )
        )
        == 1
    )
    b = buckets(workspace)
    assert (b["in_motion"]["count"], b["spending"]["count"]) == (2, 2)  # answered rows untouched


def test_remembered_expected_back_only_proposes_for_one_tap_confirmation(workspace):
    asha = counterparty(workspace)
    account, _ = three_payments(workspace, asha)
    workspace.client.post(
        "/motion/answers",
        json={"counterparty_id": asha.id, "answer": "expected_back", "remember": True},
    )
    later = move(workspace, account, date(2026, 2, 3), "-250.00", asha, key="later")

    state = motion(workspace)

    (question,) = state["questions"]
    assert question["remembered"] == "expected_back" and question["transaction_ids"] == [later.id]
    assert buckets(workspace)["in_motion"]["out"] == "3000.00"  # not applied by itself
    assert answer(workspace, asha, "expected_back").status_code == 200  # the one tap
    assert buckets(workspace)["in_motion"]["out"] == "3250.00"
    assert workspace.db.scalar(select(func.count(MoneyExpectation.id))) == 1


def test_a_remembered_choice_can_be_removed_and_nothing_already_done_changes(workspace):
    asha = counterparty(workspace)
    account, _rows = three_payments(workspace, asha)
    workspace.client.post(
        "/motion/answers", json={"counterparty_id": asha.id, "answer": "expense", "remember": True}
    )
    later = move(workspace, account, date(2026, 2, 3), "-250.00", asha, key="later")
    motion(workspace)
    preference = workspace.db.scalar(select(MotionPreference))

    removed = workspace.client.delete(f"/motion/preferences/{preference.id}")
    again = workspace.client.delete(f"/motion/preferences/{preference.id}")

    assert removed.status_code == 200 and again.status_code == 404
    assert workspace.db.scalar(select(func.count(MotionPreference.id))) == 0
    workspace.db.expire_all()
    assert workspace.db.get(TransactionMeaning, later.id).purpose == Purpose.PERSONAL  # kept
    newer = move(workspace, account, date(2026, 2, 9), "-10.00", asha, key="newer")
    (question,) = motion(workspace)["questions"]
    assert question["transaction_ids"] == [newer.id]  # asked again, no longer automatic


def test_undo_reverts_untouched_automatic_applications_and_stops_remembering(workspace):
    asha = counterparty(workspace)
    account, _ = three_payments(workspace, asha)
    workspace.client.post(
        "/motion/answers", json={"counterparty_id": asha.id, "answer": "expense", "remember": True}
    )
    kept = move(workspace, account, date(2026, 2, 3), "-250.00", asha, key="kept")
    edited = move(workspace, account, date(2026, 2, 4), "-60.00", asha, key="edited")
    motion(workspace)
    workspace.client.patch(
        f"/transactions/{edited.id}",
        json={"purpose": "income", "notes": "", "expected_version": 2},
    )
    preference = workspace.db.scalar(select(MotionPreference))

    response = workspace.client.post(f"/motion/preferences/{preference.id}/undo")

    assert response.status_code == 200
    workspace.db.expire_all()
    assert workspace.db.get(TransactionMeaning, kept.id).purpose == Purpose.OTHER
    assert (
        workspace.db.get(TransactionMeaning, edited.id).purpose != Purpose.OTHER
    )  # her later edit wins
    assert workspace.db.scalar(select(func.count(MotionPreference.id))) == 0
    assert workspace.client.post(f"/motion/preferences/{preference.id}/undo").status_code == 404


# ---- (4) loan mapping


def person(workspace, name="Meera Synthetic", user=None):
    item = Person(user_id=(user or workspace.owner).id, name=name)
    workspace.db.add(item)
    workspace.db.commit()
    return item


def loan(workspace, account, day, amount, purpose, who, key):
    transaction = move(workspace, account, day, amount, None, purpose, key=key)
    transaction.person_id = who.id
    transaction.meaning.person_id = who.id
    workspace.db.commit()
    return transaction


def loan_dataset(workspace):
    meera = person(workspace)
    account = workspace.account()
    a = loan(workspace, account, date(2026, 1, 3), "-400.00", Purpose.LOAN_GIVEN, meera, "l1")
    b = loan(workspace, account, date(2026, 1, 9), "-600.00", Purpose.LOAN_GIVEN, meera, "l2")
    c = loan(workspace, account, date(2026, 2, 1), "250.00", Purpose.LOAN_REPAYMENT, meera, "r1")
    return meera, account, (a, b, c)


def test_loans_are_proposed_grouped_by_person_and_nothing_is_converted(workspace):
    meera, _, _rows = loan_dataset(workspace)

    state = motion(workspace)

    (proposal,) = state["loan_proposals"]
    assert proposal["person_id"] == meera.id and proposal["person_name"] == "Meera Synthetic"
    assert (proposal["loans"]["count"], proposal["loans"]["total"]) == (2, "1000.00")
    assert (proposal["repayments"]["count"], proposal["repayments"]["total"]) == (1, "250.00")
    assert proposal["loan_balance_now"] == "750.00"
    assert (proposal["would_track"]["sent"], proposal["would_track"]["returned"]) == (
        "1000.00",
        "250.00",
    )
    assert workspace.db.scalar(select(func.count(MoneyExpectation.id))) == 0
    b = buckets(workspace)
    assert (b["obligations"]["count"], b["in_motion"]["count"]) == (3, 0)


def test_accepting_maps_loans_without_changing_their_purposes_and_is_replay_safe(workspace):
    meera, _, rows = loan_dataset(workspace)

    accepted = workspace.client.post("/motion/loans/accept", json={"person_id": meera.id})
    after = (
        workspace.db.scalar(select(func.count(MoneyExpectation.id))),
        workspace.db.scalar(select(func.count(ExpectationAllocation.id))),
    )
    again = workspace.client.post("/motion/loans/accept", json={"person_id": meera.id})

    assert accepted.status_code == again.status_code == 200
    assert (
        after == (1, 3)
        and (
            workspace.db.scalar(select(func.count(MoneyExpectation.id))),
            workspace.db.scalar(select(func.count(ExpectationAllocation.id))),
        )
        == after
    )
    (item,) = motion(workspace)["outstanding"]
    assert (
        item["counterparty_name"],
        item["sent"],
        item["returned"],
        item["remaining"],
        item["status"],
    ) == ("Meera Synthetic", "1000.00", "250.00", "750.00", "partly_settled")
    assert item["from_loans"] is True
    b = buckets(workspace)
    assert (b["in_motion"]["out"], b["returns"]["in"], b["obligations"]["count"]) == (
        "1000.00",
        "250.00",
        0,
    )
    workspace.db.expire_all()
    assert {workspace.db.get(Transaction, r.id).purpose for r in rows} == {
        Purpose.LOAN_GIVEN,
        Purpose.LOAN_REPAYMENT,
    }
    assert motion(workspace)["loan_proposals"] == []


def test_a_mapping_can_be_reverted_and_comes_back_as_a_proposal(workspace):
    meera, _, _ = loan_dataset(workspace)
    workspace.client.post("/motion/loans/accept", json={"person_id": meera.id})
    expectation = expectation_id(workspace)

    reverted = workspace.client.post(f"/motion/loans/{expectation}/revert")
    again = workspace.client.post(f"/motion/loans/{expectation}/revert")

    assert reverted.status_code == again.status_code == 200
    assert motion(workspace)["outstanding"] == [] and len(motion(workspace)["loan_proposals"]) == 1
    assert buckets(workspace)["obligations"]["count"] == 3
    statuses = set(workspace.db.scalars(select(ExpectationAllocation.status)))
    assert statuses == {"reverted"}  # history kept, nothing deleted


def test_repayments_beyond_what_was_lent_are_not_forced_into_the_mapping(workspace):
    meera = person(workspace)
    account = workspace.account()
    loan(workspace, account, date(2026, 1, 3), "-100.00", Purpose.LOAN_GIVEN, meera, "l1")
    loan(workspace, account, date(2026, 2, 1), "70.00", Purpose.LOAN_REPAYMENT, meera, "r1")
    loan(workspace, account, date(2026, 2, 5), "70.00", Purpose.LOAN_REPAYMENT, meera, "r2")

    response = workspace.client.post("/motion/loans/accept", json={"person_id": meera.id}).json()

    (item,) = response["outstanding"]
    assert (item["returned"], item["remaining"]) == ("70.00", "30.00")
    assert response["loan_skipped"] == 1
    assert (
        buckets(workspace)["obligations"]["count"] == 1
    )  # the part that did not fit stays as it was


def test_entrusted_and_reimbursements_are_never_converted_and_the_report_explains_why(workspace):
    meera, account, _ = loan_dataset(workspace)
    entrusted = loan(
        workspace, account, date(2026, 1, 20), "-500.00", Purpose.ENTRUSTED, meera, "e1"
    )
    refund = loan(
        workspace, account, date(2026, 1, 22), "100.00", Purpose.REIMBURSEMENT, meera, "m1"
    )
    workspace.client.post("/motion/loans/accept", json={"person_id": meera.id})

    body = report(workspace)

    assert (
        body["buckets"]["obligations"]["count"] == 2
    )  # entrusted + reimbursement stay as they were
    assert (
        "Entrusted money" in body["obligation_note"]
        and "reimbursement" in body["obligation_note"].lower()
    )
    assert "never" in body["obligation_note"].lower()
    workspace.db.expire_all()
    assert workspace.db.get(Transaction, entrusted.id).purpose == Purpose.ENTRUSTED
    assert (
        workspace.db.scalar(
            select(func.count(ExpectationAllocation.id)).where(
                ExpectationAllocation.transaction_id.in_([entrusted.id, refund.id])
            )
        )
        == 0
    )


def test_a_later_repayment_from_a_mapped_person_is_proposed_not_applied(workspace):
    meera, account, _ = loan_dataset(workspace)
    workspace.client.post("/motion/loans/accept", json={"person_id": meera.id})
    later = loan(
        workspace, account, date(2026, 3, 1), "100.00", Purpose.LOAN_REPAYMENT, meera, "r9"
    )

    (proposal,) = motion(workspace)["return_proposals"]

    assert proposal["transaction_id"] == later.id
    assert motion(workspace)["outstanding"][0]["returned"] == "250.00"


# ---- (5) investigation reads expectations


def _tracked_dataset(workspace):
    asha = counterparty(workspace)
    account = workspace.account()
    out = move(workspace, account, date(2026, 1, 5), "-1200.00", asha, key="out1200")
    move(workspace, account, date(2026, 1, 6), "-300.00", asha, key="out300")
    back = move(workspace, account, date(2026, 2, 1), "1200.00", asha, key="back1200")
    answer(workspace, asha, "expected_back")
    return asha, account, out, back


def test_an_outgoing_amount_can_be_an_open_expectation_and_an_incoming_one_a_pending_return(
    workspace,
):
    _, _, out, back = _tracked_dataset(workspace)

    answer_ = ask(workspace, "where did my 1200 go")

    claims = {c["text"]: c for c in answer_["claims"]}
    mine = next(t for t in claims if t.startswith("This is part of"))
    assert mine == (
        "This is part of ₹1,500.00 you marked as sent out and expected back since 5 Jan 2026; "
        "₹1,500.00 is still out. That is how you recorded it, not a cause."
    )
    pending = next(t for t in claims if t.startswith("This incoming"))
    assert pending == (
        "This incoming ₹1,200.00 may be a return of money you expect back; it is waiting "
        "for your confirmation."
    )
    assert (
        f"txn:{out.id}" in claims[mine]["evidence"]
        and f"txn:{back.id}" in claims[pending]["evidence"]
    )
    assert claims[mine]["kind"] == claims[pending]["kind"] == "fact"
    _check_evidence(workspace, answer_)
    assert "because" not in answer_["content"].lower()
    assert "related_links" in answer_["tools_run"] and "open_expectations" in answer_["tools_run"]


def test_confirmed_returns_written_off_items_and_links_are_stated_as_recorded(workspace):
    _, _, _out, back = _tracked_dataset(workspace)
    exp = expectation_id(workspace)
    workspace.client.post(
        "/motion/returns", json={"expectation_id": exp, "transaction_id": back.id}
    )

    confirmed = ask(workspace, "where did my 1200 go")
    assert "You confirmed this incoming ₹1,200.00 as a return of money you had sent out." in texts(
        confirmed
    )

    set_status(workspace, exp, "written_off")
    written = ask(workspace, "where did my 1200 go")
    assert (
        "This is part of ₹1,500.00 you marked as sent out and later wrote off. "
        "That is how you recorded it, not a cause."
    ) in texts(written)
    _check_evidence(workspace, written)


def test_a_linked_movement_is_reported_with_its_partner(workspace):
    account = workspace.account("1111")
    other = workspace.account("2222")
    out = move(workspace, account, date(2026, 1, 5), "-1200.00", key="o")
    inn = move(workspace, other, date(2026, 1, 6), "1200.00", key="i")
    workspace.db.add(
        TransactionLink(
            user_id=workspace.owner.id,
            link_type="own_transfer",
            outflow_transaction_id=out.id,
            inflow_transaction_id=inn.id,
            status="confirmed",
        )
    )
    workspace.db.commit()

    answer_ = ask(workspace, "where did my 1200 go")

    assert "This movement is linked to another one (own transfer) on 6 Jan 2026." in texts(answer_)
    _check_evidence(workspace, answer_)


def test_open_expectations_tool_validates_scopes_and_hides_foreign_rows(workspace):
    _, _, out, _ = _tracked_dataset(workspace)
    foreign_cp = counterparty(workspace, "Stranger Person", user=workspace.stranger)
    foreign_account = workspace.account("9999", user=workspace.stranger)
    foreign = move(
        workspace,
        foreign_account,
        date(2026, 1, 5),
        "-1200.00",
        foreign_cp,
        key="fx",
        user=workspace.stranger,
    )

    everything = tool(workspace, "open_expectations", {}).json()
    one = tool(workspace, "open_expectations", {"transaction_id": out.id}).json()

    assert (
        len(everything["expectations"]) == 1 and everything["expectations"][0]["sent"] == "1500.00"
    )
    assert one["expectations"][0]["evidence"][0] == f"txn:{out.id}"
    for bad in ({"transaction_id": "x"}, {"transaction_id": -1}, {"transaction_id": 0}):
        assert tool(workspace, "open_expectations", bad).status_code == 422
    assert tool(workspace, "open_expectations", {"transaction_id": foreign.id}).status_code == 404
    assert tool(workspace, "open_expectations", {"transaction_id": 999999}).status_code == 404


# ---- authorization, idempotency, privacy for the new en


def test_every_new_endpoint_hides_foreign_ids_and_requires_sign_in(workspace):
    foreign_cp = counterparty(workspace, "Stranger Person", user=workspace.stranger)
    stranger_person = person(workspace, "Stranger Meera", user=workspace.stranger)
    foreign_pref = MotionPreference(
        user_id=workspace.stranger.id,
        counterparty_id=foreign_cp.id,
        choice="expense",
        after_transaction_id=0,
    )
    foreign_exp = MoneyExpectation(
        user_id=workspace.stranger.id,
        kind="person",
        person_id=stranger_person.id,
        status="tracking",
        opened_on=date(2026, 1, 1),
    )
    workspace.db.add_all([foreign_pref, foreign_exp])
    workspace.db.commit()

    assert (
        workspace.client.post(
            "/motion/answers",
            json={"counterparty_id": foreign_cp.id, "answer": "expense", "remember": True},
        ).status_code
        == 404
    )
    assert workspace.client.delete(f"/motion/preferences/{foreign_pref.id}").status_code == 404
    assert workspace.client.post(f"/motion/preferences/{foreign_pref.id}/undo").status_code == 404
    assert (
        workspace.client.post(
            "/motion/loans/accept", json={"person_id": stranger_person.id}
        ).status_code
        == 404
    )
    assert workspace.client.post(f"/motion/loans/{foreign_exp.id}/revert").status_code == 404
    assert workspace.db.scalar(select(func.count(MotionPreference.id))) == 1
    workspace.client.cookies.clear()
    for method, path in (
        ("get", "/motion"),
        ("post", "/motion/loans/accept"),
        ("delete", "/motion/preferences/1"),
        ("post", "/motion/preferences/1/undo"),
        ("post", "/motion/loans/1/revert"),
    ):
        assert getattr(workspace.client, method)(path).status_code in (401, 422)


def test_new_flows_log_nothing_sensitive(workspace, privacy_probe):
    asha = counterparty(workspace, "ZEBRAQUOKKA PERSON")
    meera = person(workspace, "ZEBRAQUOKKA LENDEE")
    account, _ = three_payments(workspace, asha)
    loan(workspace, account, date(2026, 2, 1), "-777.00", Purpose.LOAN_GIVEN, meera, "lx")
    workspace.client.post(
        "/motion/answers", json={"counterparty_id": asha.id, "answer": "expense", "remember": True}
    )
    motion(workspace)
    workspace.client.post("/motion/loans/accept", json={"person_id": meera.id})
    set_status(workspace, expectation_id(workspace), "written_off")
    for message in ("what is still out?", "where did my 777 go"):
        workspace.client.post("/assistant/turn", json={"message": message})
    privacy_probe.record("answer", ask(workspace, "where did my 777 go"))
    privacy_probe.assert_clean("ZEBRAQUOKKA")


@pytest.mark.parametrize("bucket", ["written_off"])
def test_written_off_bucket_drills_down_to_its_rows(workspace, bucket):
    asha = counterparty(workspace)
    three_payments(workspace, asha)
    answer(workspace, asha, "expected_back")
    set_status(workspace, expectation_id(workspace), "written_off")

    rows = workspace.client.get(
        "/reports/period/rows", params={"from": "2026-01-01", "to": "2026-03-31", "bucket": bucket}
    ).json()

    assert rows["total"] == 3 and Decimal(rows["items"][0]["amount"]) == Decimal("-1000.00")
