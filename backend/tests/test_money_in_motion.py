from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import (
    Counterparty,
    ExpectationAllocation,
    MoneyExpectation,
    Purpose,
    Transaction,
    TransactionLink,
    TransactionMeaning,
)

ASHA = "Asha Synthetic"


def counterparty(workspace, name=ASHA, user=None):
    owner = user or workspace.owner
    item = Counterparty(user_id=owner.id, display_name=name)
    workspace.db.add(item)
    workspace.db.commit()
    return item


def move(
    workspace,
    account,
    day,
    amount,
    cp=None,
    purpose=Purpose.OTHER,
    key=None,
    user=None,
    reference="",
    description="SYNTH UPI",
):
    owner = user or workspace.owner
    transaction = Transaction(
        user_id=owner.id,
        account_id=account.id,
        txn_date=day,
        description=description,
        amount=Decimal(amount),
        purpose=purpose,
        reference=reference,
        dedupe_hash=key or f"{description}-{day}-{amount}-{reference}-{account.id}",
    )
    transaction.meaning = TransactionMeaning(
        user_id=owner.id,
        purpose=purpose,
        counterparty_id=cp.id if cp else None,
        decision_source="unresolved" if purpose == Purpose.OTHER else "user_confirmed",
    )
    workspace.db.add(transaction)
    workspace.db.commit()
    return transaction


def three_payments(workspace, cp, hint="1111"):
    account = workspace.account(hint)
    rows = [move(workspace, account, date(2026, 1, d), "-1000.00", cp) for d in (3, 10, 17)]
    return account, rows


def report(workspace):
    return workspace.client.get(
        "/reports/period", params={"from": "2026-01-01", "to": "2026-03-31"}
    ).json()


def buckets(workspace):
    return report(workspace)["buckets"]


def motion(workspace):
    return workspace.client.get("/motion").json()


def answer(workspace, cp, choice, rows=None):
    body = {"counterparty_id": cp.id, "answer": choice}
    if rows is not None:
        body["transaction_ids"] = [row.id for row in rows]
    return workspace.client.post("/motion/answers", json=body)


def counts(workspace):
    return (
        workspace.db.scalar(select(func.count(MoneyExpectation.id))),
        workspace.db.scalar(select(func.count(ExpectationAllocation.id))),
        workspace.db.scalar(select(func.count(TransactionMeaning.transaction_id))),
    )


# ---- the default: unknown until she says -----------------------------------------------------


def test_payments_to_a_person_are_unknown_until_she_says(workspace):
    asha = counterparty(workspace)
    three_payments(workspace, asha)

    state = motion(workspace)
    b = buckets(workspace)

    (question,) = state["questions"]
    assert (question["counterparty_id"], question["count"], question["total"]) == (
        asha.id,
        3,
        "3000.00",
    )
    assert (question["first_date"], question["last_date"]) == ("2026-01-03", "2026-01-17")
    assert b["unexplained"]["out"] == "3000.00"
    assert b["spending"]["count"] == b["in_motion"]["count"] == b["returns"]["count"] == 0
    assert state["outstanding"] == []
    assert (
        workspace.db.scalar(select(func.count(MoneyExpectation.id))) == 0
    )  # nothing created by guess


def test_card_and_cash_and_already_classified_rows_are_not_asked_about(workspace):
    asha = counterparty(workspace)
    bank = workspace.account("1111")
    card = workspace.account("2222")
    workspace.db.get(type(card), card.id).instrument_type = "credit_card"
    workspace.db.commit()
    move(workspace, card, date(2026, 1, 2), "-50.00", asha, key="card")
    move(workspace, bank, date(2026, 1, 3), "-60.00", asha, Purpose.PERSONAL, key="classified")
    move(workspace, bank, date(2026, 1, 4), "-70.00", None, key="nobody")  # no counterparty at all
    move(workspace, bank, date(2026, 1, 5), "80.00", asha, key="incoming")

    assert motion(workspace)["questions"] == []


def test_expected_back_tracks_it_without_calling_it_spending(workspace):
    asha = counterparty(workspace)
    _, rows = three_payments(workspace, asha)

    response = answer(workspace, asha, "expected_back")

    assert response.status_code == 200
    b = buckets(workspace)
    assert (b["in_motion"]["count"], b["in_motion"]["out"]) == (3, "3000.00")
    assert b["spending"]["count"] == 0 and b["unexplained"]["count"] == 0
    (item,) = motion(workspace)["outstanding"]
    assert (item["counterparty_name"], item["sent"], item["returned"], item["remaining"]) == (
        ASHA,
        "3000.00",
        "0.00",
        "3000.00",
    )
    assert (item["status"], item["since"]) == ("open", "2026-01-03")
    assert item["evidence"] == [f"txn:{r.id}" for r in rows]
    assert motion(workspace)["questions"] == []


def test_expense_is_recorded_only_because_she_said_so(workspace):
    asha = counterparty(workspace)
    _, rows = three_payments(workspace, asha)

    assert answer(workspace, asha, "expense").status_code == 200

    b = buckets(workspace)
    assert (b["spending"]["count"], b["spending"]["out"]) == (3, "3000.00")
    assert b["in_motion"]["count"] == 0
    meaning = workspace.db.scalar(
        select(TransactionMeaning).where(TransactionMeaning.transaction_id == rows[0].id)
    )
    assert (meaning.purpose, meaning.decision_source) == (Purpose.PERSONAL, "user_confirmed")
    assert workspace.db.scalar(select(func.count(MoneyExpectation.id))) == 0


def test_answering_can_cover_only_some_of_the_payments(workspace):
    asha = counterparty(workspace)
    _, rows = three_payments(workspace, asha)

    answer(workspace, asha, "expected_back", rows[:1])

    b = buckets(workspace)
    assert (b["in_motion"]["count"], b["unexplained"]["count"]) == (1, 2)
    (question,) = motion(workspace)["questions"]
    assert question["count"] == 2


def test_replaying_or_double_submitting_an_answer_changes_nothing(workspace):
    asha = counterparty(workspace)
    three_payments(workspace, asha)
    answer(workspace, asha, "expected_back")
    after = counts(workspace)

    again = answer(workspace, asha, "expected_back")

    assert again.status_code == 200
    assert counts(workspace) == after
    assert workspace.db.scalar(select(func.count(MoneyExpectation.id))) == 1
    asha_two = counterparty(workspace, "Ravi Synthetic")
    _, rows = three_payments(workspace, asha_two, "3333")
    answer(workspace, asha_two, "expense")
    version = workspace.db.scalar(
        select(TransactionMeaning.version).where(TransactionMeaning.transaction_id == rows[0].id)
    )
    answer(workspace, asha_two, "expense")
    assert (
        workspace.db.scalar(
            select(TransactionMeaning.version).where(
                TransactionMeaning.transaction_id == rows[0].id
            )
        )
        == version
    )


def test_a_later_payment_to_the_same_person_joins_the_open_expectation_only_after_she_says(
    workspace,
):
    asha = counterparty(workspace)
    account, _ = three_payments(workspace, asha)
    answer(workspace, asha, "expected_back")
    move(workspace, account, date(2026, 2, 2), "-500.00", asha, key="later")

    (question,) = motion(workspace)["questions"]
    assert (question["count"], question["total"]) == (1, "500.00")
    assert buckets(workspace)["in_motion"]["out"] == "3000.00"  # the new one is still unknown
    answer(workspace, asha, "expected_back")
    assert buckets(workspace)["in_motion"]["out"] == "3500.00"
    assert workspace.db.scalar(select(func.count(MoneyExpectation.id))) == 1


# ---- returns ---------------------------------------------------------------------------------


def test_a_return_is_never_income_and_is_settled_only_when_she_confirms(workspace):
    asha = counterparty(workspace)
    account, _ = three_payments(workspace, asha)
    answer(workspace, asha, "expected_back")
    back = move(workspace, account, date(2026, 2, 1), "1000.00", asha, key="back1")

    b = buckets(workspace)
    assert b["income"]["count"] == 0 and b["returns"]["count"] == 0  # unconfirmed: just unexplained
    assert b["unexplained"]["in"] == "1000.00"
    (proposal,) = motion(workspace)["return_proposals"]
    assert (proposal["transaction_id"], proposal["amount"], proposal["remaining"]) == (
        back.id,
        "1000.00",
        "3000.00",
    )

    confirm = workspace.client.post(
        "/motion/returns",
        json={"expectation_id": proposal["expectation_id"], "transaction_id": back.id},
    )
    assert confirm.status_code == 200
    b = buckets(workspace)
    assert (b["returns"]["count"], b["returns"]["in"]) == (1, "1000.00")
    assert b["income"]["count"] == 0 and b["unexplained"]["in"] == "0.00"
    (item,) = motion(workspace)["outstanding"]
    assert (item["status"], item["returned"], item["remaining"]) == (
        "partly_settled",
        "1000.00",
        "2000.00",
    )
    assert item["returns"] == [f"txn:{back.id}"]


def test_full_return_settles_it(workspace):
    asha = counterparty(workspace)
    account, _ = three_payments(workspace, asha)
    answer(workspace, asha, "expected_back")
    back = move(workspace, account, date(2026, 2, 1), "3000.00", asha, key="back")
    expectation = motion(workspace)["outstanding"][0]["expectation_id"]

    workspace.client.post(
        "/motion/returns", json={"expectation_id": expectation, "transaction_id": back.id}
    )

    item = motion(workspace)["outstanding"][0]
    assert (item["status"], item["remaining"]) == ("settled", "0.00")


def test_allocating_more_than_a_movement_is_rejected_atomically(workspace):
    asha = counterparty(workspace)
    account, _ = three_payments(workspace, asha)
    answer(workspace, asha, "expected_back")
    back = move(workspace, account, date(2026, 2, 1), "1000.00", asha, key="back")
    expectation = motion(workspace)["outstanding"][0]["expectation_id"]
    workspace.client.post(
        "/motion/returns",
        json={"expectation_id": expectation, "transaction_id": back.id, "amount": "600.00"},
    )
    before = counts(workspace)

    too_much = workspace.client.post(
        "/motion/returns",
        json={"expectation_id": expectation, "transaction_id": back.id, "amount": "500.00"},
    )

    assert too_much.status_code == 409
    assert counts(workspace) == before
    assert Decimal(motion(workspace)["outstanding"][0]["returned"]) == Decimal("600.00")
    sent_cap = workspace.client.post(
        "/motion/returns",
        json={"expectation_id": expectation, "transaction_id": back.id, "amount": "400.00"},
    )
    assert sent_cap.status_code == 200


def test_a_return_larger_than_what_is_expected_is_not_silently_clipped(workspace):
    asha = counterparty(workspace)
    account, rows = three_payments(workspace, asha)
    answer(workspace, asha, "expected_back", rows[:1])  # only 1000 expected
    back = move(workspace, account, date(2026, 2, 1), "1500.00", asha, key="back")
    expectation = motion(workspace)["outstanding"][0]["expectation_id"]

    over = workspace.client.post(
        "/motion/returns",
        json={"expectation_id": expectation, "transaction_id": back.id, "amount": "1500.00"},
    )

    assert over.status_code == 409
    assert "more than is still expected back" in over.json()["detail"]


def test_a_confirmed_return_can_be_reverted_and_a_dismissed_one_is_not_proposed_again(workspace):
    asha = counterparty(workspace)
    account, _ = three_payments(workspace, asha)
    answer(workspace, asha, "expected_back")
    first = move(workspace, account, date(2026, 2, 1), "1000.00", asha, key="b1")
    second = move(workspace, account, date(2026, 2, 5), "200.00", asha, key="b2")
    expectation = motion(workspace)["outstanding"][0]["expectation_id"]
    workspace.client.post(
        "/motion/returns", json={"expectation_id": expectation, "transaction_id": first.id}
    )
    allocation = workspace.db.scalar(
        select(ExpectationAllocation).where(ExpectationAllocation.transaction_id == first.id)
    )

    reverted = workspace.client.post(f"/motion/allocations/{allocation.id}/revert")
    dismissed = workspace.client.post(
        "/motion/returns/dismiss", json={"expectation_id": expectation, "transaction_id": second.id}
    )

    assert reverted.status_code == 200 and dismissed.status_code == 200
    assert Decimal(motion(workspace)["outstanding"][0]["returned"]) == 0
    assert [p["transaction_id"] for p in motion(workspace)["return_proposals"]] == [first.id]
    assert buckets(workspace)["returns"]["count"] == 0
    # reverting twice changes nothing
    assert workspace.client.post(f"/motion/allocations/{allocation.id}/revert").status_code == 200


def test_written_off_and_not_expected_are_her_decisions_and_reversible(workspace):
    asha = counterparty(workspace)
    three_payments(workspace, asha)
    answer(workspace, asha, "expected_back")
    expectation = motion(workspace)["outstanding"][0]["expectation_id"]

    for status in ("written_off", "not_expected", "open"):
        response = workspace.client.post(
            f"/motion/expectations/{expectation}/status", json={"status": status}
        )
        assert response.status_code == 200
        assert motion(workspace)["outstanding"][0]["status"] == status
    assert (
        workspace.client.post(
            f"/motion/expectations/{expectation}/status", json={"status": "bogus"}
        ).status_code
        == 422
    )


# ---- own-account transfers ----------------------------------------------------------------------


def test_exact_transfer_with_shared_evidence_is_linked_and_neutral(workspace):
    savings, current = workspace.account("1111"), workspace.account("2222")
    out = move(
        workspace,
        savings,
        date(2026, 1, 5),
        "-5000.00",
        reference="TRF778899",
        description="NEFT TO OWN",
        key="o1",
    )
    inn = move(
        workspace,
        current,
        date(2026, 1, 6),
        "5000.00",
        reference="TRF778899",
        description="NEFT FROM OWN",
        key="i1",
    )

    workspace.client.get("/motion")

    link = workspace.db.scalar(select(TransactionLink))
    assert (link.link_type, link.status, link.decision_source) == (
        "own_transfer",
        "confirmed",
        "automatic_rule",
    )
    assert (link.outflow_transaction_id, link.inflow_transaction_id) == (out.id, inn.id)
    b = buckets(workspace)
    assert (b["own_places"]["out"], b["own_places"]["in"]) == ("5000.00", "5000.00")
    assert b["unexplained"]["count"] == 0 and b["income"]["count"] == 0


def test_weak_evidence_is_only_proposed_and_ambiguity_links_nothing(workspace):
    savings, current = workspace.account("1111"), workspace.account("2222")
    out = move(workspace, savings, date(2026, 1, 5), "-700.00", key="o1")
    inn = move(workspace, current, date(2026, 1, 7), "700.00", key="i1")

    state = motion(workspace)

    assert workspace.db.scalar(select(func.count(TransactionLink.id))) == 0
    (proposal,) = state["transfer_proposals"]
    assert (proposal["outflow_id"], proposal["inflow_id"]) == (out.id, inn.id)
    assert buckets(workspace)["own_places"]["count"] == 0
    confirmed = workspace.client.post(
        "/motion/transfers", json={"outflow_id": out.id, "inflow_id": inn.id}
    )
    assert confirmed.status_code == 200
    assert buckets(workspace)["own_places"]["count"] == 2
    link = workspace.db.scalar(select(TransactionLink))
    assert link.decision_source == "user_confirmed"

    # Two equally plausible incoming rows: nothing is guessed, nothing is proposed.
    out2 = move(workspace, savings, date(2026, 2, 5), "-900.00", key="o2")
    move(workspace, current, date(2026, 2, 6), "900.00", key="i2a")
    move(workspace, current, date(2026, 2, 7), "900.00", key="i2b")
    state = motion(workspace)
    assert all(p["outflow_id"] != out2.id for p in state["transfer_proposals"])
    assert workspace.db.scalar(select(func.count(TransactionLink.id))) == 1


def test_transfers_never_pair_with_another_users_rows_or_the_same_account(workspace):
    mine = workspace.account("1111")
    theirs = workspace.account("2222", user=workspace.stranger)
    move(workspace, mine, date(2026, 1, 5), "-400.00", reference="REF1", key="o")
    move(
        workspace,
        theirs,
        date(2026, 1, 5),
        "400.00",
        reference="REF1",
        key="i",
        user=workspace.stranger,
    )
    move(workspace, mine, date(2026, 1, 5), "400.00", reference="REF1", key="same-account-in")

    state = motion(workspace)

    assert state["transfer_proposals"] == []
    assert workspace.db.scalar(select(func.count(TransactionLink.id))) == 0


# ---- authorization, assistant, privacy ---------------------------------------------------------


def test_foreign_counterparties_expectations_allocations_and_transactions_are_404(workspace):
    foreign_cp = counterparty(workspace, "Stranger Person", user=workspace.stranger)
    foreign_account = workspace.account("9999", user=workspace.stranger)
    foreign_txn = move(
        workspace,
        foreign_account,
        date(2026, 1, 3),
        "-100.00",
        foreign_cp,
        key="fx",
        user=workspace.stranger,
    )
    foreign_expectation = MoneyExpectation(
        user_id=workspace.stranger.id,
        kind="person",
        counterparty_id=foreign_cp.id,
        status="tracking",
        opened_on=date(2026, 1, 3),
    )
    workspace.db.add(foreign_expectation)
    workspace.db.flush()
    allocation = ExpectationAllocation(
        user_id=workspace.stranger.id,
        expectation_id=foreign_expectation.id,
        transaction_id=foreign_txn.id,
        role="out",
        amount=Decimal("100.00"),
        status="confirmed",
        decision_source="user_confirmed",
    )
    workspace.db.add(allocation)
    workspace.db.commit()

    assert answer(workspace, foreign_cp, "expected_back").status_code == 404
    assert (
        workspace.client.post(
            "/motion/returns",
            json={"expectation_id": foreign_expectation.id, "transaction_id": foreign_txn.id},
        ).status_code
        == 404
    )
    assert (
        workspace.client.post(
            "/motion/returns/dismiss",
            json={"expectation_id": foreign_expectation.id, "transaction_id": foreign_txn.id},
        ).status_code
        == 404
    )
    assert workspace.client.post(f"/motion/allocations/{allocation.id}/revert").status_code == 404
    assert (
        workspace.client.post(
            f"/motion/expectations/{foreign_expectation.id}/status", json={"status": "written_off"}
        ).status_code
        == 404
    )
    assert (
        workspace.client.post(
            "/motion/transfers", json={"outflow_id": foreign_txn.id, "inflow_id": foreign_txn.id}
        ).status_code
        == 404
    )
    mine = counterparty(workspace)
    assert answer(workspace, mine, "expected_back", [foreign_txn]).status_code == 404
    state = motion(workspace)
    assert state["questions"] == [] and state["outstanding"] == []
    workspace.db.refresh(allocation)
    assert allocation.status == "confirmed"


def test_endpoints_require_sign_in_and_validate_answers(workspace):
    asha = counterparty(workspace)
    three_payments(workspace, asha)
    assert answer(workspace, asha, "maybe").status_code == 422
    workspace.client.cookies.clear()
    assert workspace.client.get("/motion").status_code == 401
    assert workspace.client.post("/motion/answers", json={}).status_code == 401


def test_assistant_says_what_is_still_out_with_evidence_and_never_invents(workspace):
    quiet = workspace.client.post("/assistant/turn", json={"message": "what is still out?"}).json()
    assert "Nothing is marked as sent out and expected back" in quiet["content"]
    asha = counterparty(workspace)
    account, rows = three_payments(workspace, asha)
    answer(workspace, asha, "expected_back")
    move(workspace, account, date(2026, 2, 1), "1000.00", asha, key="b")
    expectation = motion(workspace)["outstanding"][0]["expectation_id"]
    back = workspace.db.scalar(select(Transaction).where(Transaction.dedupe_hash == "b"))
    workspace.client.post(
        "/motion/returns", json={"expectation_id": expectation, "transaction_id": back.id}
    )

    reply = workspace.client.post("/assistant/turn", json={"message": "what is still out?"}).json()

    assert reply["context"]["kind"] == "motion_summary"
    assert f"{ASHA}: ₹2,000.00 still out of ₹3,000.00 sent since 3 Jan 2026" in reply["content"]
    assert "₹1,000.00 has come back" in reply["content"]
    assert reply["context"]["evidence"] == [f"txn:{r.id}" for r in rows] + [f"txn:{back.id}"]


def test_money_in_motion_logs_nothing_sensitive(workspace, privacy_probe):
    asha = counterparty(workspace, "ZEBRAQUOKKA PERSON")
    account, _ = three_payments(workspace, asha)
    move(
        workspace,
        account,
        date(2026, 2, 1),
        "100.00",
        asha,
        key="b",
        description="ZEBRAQUOKKA NARRATION",
    )
    answer(workspace, asha, "expected_back")
    for path in ("/motion", "/reports/period?from=2026-01-01&to=2026-03-31"):
        workspace.client.get(path)
    workspace.client.post("/assistant/turn", json={"message": "what is still out?"})
    workspace.client.post("/motion/returns", json={"expectation_id": 999, "transaction_id": 999})

    privacy_probe.assert_clean("ZEBRAQUOKKA", "3000.00")


@pytest.mark.parametrize("bucket", ["in_motion", "returns"])
def test_new_buckets_have_labels_and_drill_down(workspace, bucket):
    asha = counterparty(workspace)
    account, _ = three_payments(workspace, asha)
    answer(workspace, asha, "expected_back")
    back = move(workspace, account, date(2026, 2, 1), "1000.00", asha, key="b")
    expectation = motion(workspace)["outstanding"][0]["expectation_id"]
    workspace.client.post(
        "/motion/returns", json={"expectation_id": expectation, "transaction_id": back.id}
    )

    body = report(workspace)
    rows = workspace.client.get(
        "/reports/period/rows", params={"from": "2026-01-01", "to": "2026-03-31", "bucket": bucket}
    ).json()

    assert body["labels"][bucket]
    assert len(rows["items"]) == body["buckets"][bucket]["count"] > 0
    dashboard = workspace.client.get(
        "/dashboard", params={"from": "2026-01-01", "to": "2026-03-31"}
    ).json()
    assert dashboard["period"]["buckets"] == body["buckets"]
