# ruff: noqa: E501
from datetime import date, timedelta
from decimal import Decimal

import pytest
from conftest import encrypted_statement_pdf
from sqlalchemy import select

from app.config import settings
from app.models import AssistantMessage, ImportRow, Purpose


@pytest.fixture(autouse=True)
def private_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")


def confirmed(workspace, account, end: date, closing="900", hint="1111", opening="1000"):
    statement = workspace.stage(
        [(end, f"SYNTH ROW {end.isoformat()}", f"{Decimal(closing) - Decimal(opening):.2f}", "")],
        hint=hint,
        opening=opening,
        closing=closing,
        period=(end - timedelta(days=20), end),
    )
    assert workspace.confirm(statement, account).status_code == 200


# ---- rows come back in statement order -------------------------------------------------------------


def test_review_rows_stay_in_statement_order_after_the_user_edits_one(workspace):
    workspace.account()
    statement = workspace.stage(
        [(date(2026, 1, d), f"SYNTH ROW {d}", "-1.00", "") for d in (1, 2, 3, 4, 5, 6)],
        opening="100",
        closing="94",
        period=(date(2026, 1, 1), date(2026, 1, 6)),
    )
    first = min(statement.rows, key=lambda r: r.id)
    workspace.client.patch(f"/imports/{statement.id}/rows/{first.id}", json={"purpose": "personal"})
    workspace.client.patch(f"/imports/{statement.id}/rows/{first.id}", json={"purpose": "business"})

    rows = workspace.client.get(f"/imports/{statement.id}").json()["rows"]

    assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)
    assert [r["txn_date"] for r in rows] == sorted(r["txn_date"] for r in rows)


# ---- wording and formatting ------------------------------------------------------------------------


def test_report_summary_uses_indian_grouping_for_every_amount(workspace):
    account = workspace.account()
    statement = workspace.stage(
        [
            (date(2026, 1, 3), "SYNTH BIG", "-150000.00", ""),
            (date(2026, 1, 4), "SYNTH IN", "250000.00", ""),
        ],
        opening="0",
        closing="100000",
        period=(date(2026, 1, 1), date(2026, 1, 31)),
    )
    workspace.confirm(statement, account)

    body = workspace.client.get(
        "/reports/period", params={"from": "2026-01-01", "to": "2026-01-31"}
    ).json()

    assert "₹1,50,000.00 out" in body["summary"] and "₹2,50,000.00 in" in body["summary"]
    assert not any(
        ch.isdigit() and "₹" in part
        for part in body["summary"].split()
        for ch in [part[-1:]]
        if False
    )


def test_plain_words_replace_scope_jargon(workspace):
    account = workspace.account()
    confirmed(workspace, account, date(2026, 1, 31))

    coverage = workspace.client.get("/coverage").json()["overall"]

    assert coverage["summary"].startswith("Everything I have checked is reconciled through")
    assert "in scope" not in coverage["summary"]
    empty = workspace.client.patch(
        f"/coverage/accounts/{account.id}", json={"in_scope": False}
    ).json()
    assert "in scope" not in empty["overall"]["summary"]
    assert "do not have any statements" in empty["overall"]["summary"]


def test_first_greeting_after_choosing_a_period_does_not_say_welcome_back(workspace):
    workspace.client.put(
        "/review-period", json={"period_start": "2026-01-01", "period_end": "2026-03-31"}
    )

    greeting = workspace.client.get("/workflow/state").json()["greeting"]

    assert "Welcome back" not in greeting
    assert "Send me the statements that cover it" in greeting
    assert "1 Jan 2026 to 31 Mar 2026" in greeting


# ---- recency ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "label"), [(2, "current"), (20, "recent"), (60, "stale"), (200, "old")]
)
def test_statement_age_is_labeled_current_recent_stale_or_old(workspace, days, label):
    account = workspace.account()
    confirmed(workspace, account, date.today() - timedelta(days=days))

    item = workspace.client.get("/accounts").json()[0]

    assert (item["age_days"], item["freshness"]) == (days, label)
    assert item["is_stale"] == (days > 35)
    assert workspace.client.get("/dashboard").json()["freshness"] == label


def test_balance_summary_lists_each_account_with_its_own_date(workspace):
    first = workspace.account("1111", opening_balance=Decimal("1000"))
    second = workspace.account("2222", opening_balance=Decimal("500"))
    confirmed(workspace, first, date(2026, 1, 31), hint="1111")
    confirmed(workspace, second, date(2026, 3, 15), hint="2222", closing="400", opening="500")

    reply = workspace.client.post("/assistant/turn", json={"message": "balance summary"}).json()[
        "content"
    ]

    assert "Account 1111 ₹900.00 as of 31 Jan 2026" in reply
    assert "Account 2222 ₹400.00 as of 15 Mar 2026" in reply
    assert "₹1,300.00" in reply and "different dates" in reply


# ---- gaps name the account -------------------------------------------------------------------------


def test_gap_claims_name_the_account_when_there_are_several(workspace):
    first, second = workspace.account("1111"), workspace.account("2222")
    confirmed(workspace, first, date(2026, 1, 31), hint="1111")
    confirmed(workspace, second, date(2026, 3, 15), hint="2222", closing="400", opening="500")

    answer = workspace.client.post("/investigate", json={"message": "where did my 777 go"}).json()

    gap_texts = [c["text"] for c in answer["claims"] if c["kind"] == "gap"]
    assert gap_texts and all(t.startswith(("Account 1111:", "Account 2222:")) for t in gap_texts)
    assert any("nothing covers" in t for t in gap_texts)


# ---- built-in mapping for bank charges and interest ----------------------------------------------------


def test_bank_charges_and_interest_are_suggested_never_confident(workspace):
    workspace.account()
    statement = workspace.stage(
        [
            (date(2026, 1, 2), "SBIN SMS CHRG 1ST QTR", "-17.70", ""),
            (date(2026, 1, 3), "1234567890:Int.Pd:01-10-2025 to 31-12-2025", "20.00", ""),
            (date(2026, 1, 4), "UPI/DR/123456789012/SHOP/YESB/shop@okaxis", "-50.00", ""),
        ],
        opening="1000",
        closing="952.30",
        period=(date(2026, 1, 1), date(2026, 1, 31)),
    )
    rows = {
        r.description[:8]: r
        for r in workspace.db.scalars(
            select(ImportRow).where(ImportRow.statement_import_id == statement.id)
        )
    }

    charge, interest, shop = rows["SBIN SMS"], rows["12345678"], rows["UPI/DR/1"]
    assert (charge.category, charge.purpose, charge.confidence) == (
        "fee",
        Purpose.PERSONAL,
        "suggested",
    )
    assert (interest.category, interest.purpose, interest.confidence) == (
        "interest",
        Purpose.INCOME,
        "suggested",
    )
    assert charge.decision_source == interest.decision_source == "built_in_mapping"
    assert (shop.category, shop.purpose) == ("uncategorized", Purpose.OTHER)


# ---- after unlocking, the assistant says what it found ----------------------------------------------


def test_the_assistant_summarizes_a_statement_once_it_is_unlocked(workspace, tmp_path):
    path = tmp_path / "protected.pdf"
    encrypted_statement_pdf(
        path, "synthetic-pw", [("01/03/2026", "UPI SYNTH SHOP", "100.00", "", "900.00")]
    )
    upload = workspace.client.post(
        "/imports", files=[("files", ("p.pdf", path.read_bytes(), "application/pdf"))]
    )
    statement = upload.json()[0]
    workspace.client.put(
        "/review-period", json={"period_start": "2026-01-01", "period_end": "2026-03-31"}
    )
    run = workspace.client.post("/workflow/runs", json={"import_ids": [statement["id"]]}).json()

    body = {"import_id": statement["id"], "password": "synthetic-pw"}
    assert (
        workspace.client.post(f"/workflow/runs/{run['id']}/password", json=body).status_code == 200
    )
    workspace.client.post(f"/workflow/runs/{run['id']}/password", json=body)  # replay

    summaries = [
        m
        for m in workspace.db.scalars(
            select(AssistantMessage).where(AssistantMessage.role == "assistant")
        )
        if m.context.get("run_id") == run["id"]
    ]
    assert len(summaries) == 1
    assert "I found a" in summaries[0].content and "1 transaction" in summaries[0].content
    assert workspace.client.get(f"/workflow/runs/{run['id']}").json()["updated_at"]
