import pytest
from sqlalchemy import select

from app.config import settings
from app.models import StatementImport
from app.parsers import ParseError

# Distinctive synthetic values; none of them may ever appear in logs or error diagnostics.
PAYEE = "ZEBRAQUOKKA PAYEE"
NAME = "Priyanka Synthetic-Holder"
ACCOUNT_NUMBER = "5550123456789"
AMOUNT = "4321.55"
PASSWORD = "hunter2-synthetic-pw"
SECRETS = (PAYEE, NAME, ACCOUNT_NUMBER, AMOUNT, "4,321.55", PASSWORD, "ZEBRAQUOKKA")

STATEMENT = (
    f"HDFC Bank Statement,{NAME},Account No: {ACCOUNT_NUMBER},,\n"
    "Date,Narration,Withdrawal,Deposit,Closing Balance\n"
    f"01/04/2026,UPI {PAYEE},{AMOUNT},,95678.45\n"
    "02/04/2026,SYNTHETIC SALARY,,50000.00,145678.45\n"
)
UNSUPPORTED = f"{NAME} {ACCOUNT_NUMBER}\n{PAYEE} {AMOUNT}\nnothing that looks like a table\n"


@pytest.fixture(autouse=True)
def private_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")


def _upload(workspace, content: str, name: str = "statement.csv", mime: str = "text/csv"):
    return workspace.client.post("/imports", files=[("files", (name, content.encode(), mime))])


def test_upload_review_confirm_and_assistant_turn_log_nothing_sensitive(workspace, privacy_probe):
    upload = _upload(workspace, STATEMENT)
    assert upload.status_code == 201
    statement = upload.json()[0]
    assert statement["status"] == "ready_for_review"

    confirm = workspace.client.post(
        f"/imports/{statement['id']}/confirm",
        json={"account_name": "Synthetic account", "bank_slug": "hdfc"},
    )
    assert confirm.status_code == 200
    turn = workspace.client.post(
        "/assistant/turn", json={"message": "", "import_ids": [statement["id"]]}
    )
    assert turn.status_code == 201
    for question in ("balance summary", "who did I pay", "card payments", "unresolved"):
        assert (
            workspace.client.post("/assistant/turn", json={"message": question}).status_code == 201
        )
    for path in ("/dashboard", "/accounts", "/transactions", "/imports/possibly-missed-rows"):
        assert workspace.client.get(path).status_code == 200

    privacy_probe.assert_clean(*SECRETS)


def test_unsupported_layout_failure_stores_and_logs_no_statement_text(workspace, privacy_probe):
    upload = _upload(workspace, UNSUPPORTED)

    statement = upload.json()[0]
    assert statement["status"] == "failed"
    stored = workspace.db.scalar(select(StatementImport)).error_message
    privacy_probe.record("stored_error_message", stored)
    privacy_probe.record("api_error_message", statement["error_message"])
    privacy_probe.record_response("upload_response_if_error", upload)
    turn = workspace.client.post(
        "/assistant/turn", json={"message": "", "import_ids": [statement["id"]]}
    )
    privacy_probe.record("assistant_failure_reply", turn.json()["content"])

    privacy_probe.assert_clean(*SECRETS)


def test_corrupt_pdf_failure_logs_no_content(workspace, privacy_probe):
    body = f"%PDF-1.4 {PAYEE} {NAME} {ACCOUNT_NUMBER} not really a pdf"
    upload = _upload(workspace, body, "statement.pdf", "application/pdf")

    statement = upload.json()[0]
    assert statement["status"] in {"failed", "password_required"}
    privacy_probe.record("stored_error_message", statement["error_message"])
    privacy_probe.assert_clean(*SECRETS)


def test_password_flow_never_stores_or_logs_the_password(workspace, privacy_probe, monkeypatch):
    calls = []

    def locked(path, password=None):
        calls.append(password)
        raise ParseError("This PDF is password protected.")

    monkeypatch.setattr("app.imports.parse_statement", locked)
    upload = _upload(workspace, f"%PDF-1.4 {PAYEE}", "statement.pdf", "application/pdf")
    statement = upload.json()[0]
    assert statement["status"] == "password_required"

    wrong = workspace.client.post(f"/imports/{statement['id']}/unlock", json={"password": PASSWORD})
    assert wrong.status_code == 422
    assert calls[-1] == PASSWORD  # it was used for the attempt, request-scoped only
    privacy_probe.record_response("unlock_failure", wrong)
    stored = workspace.db.scalar(select(StatementImport))
    privacy_probe.record("stored_statement_row", [stored.error_message, stored.storage_key])
    privacy_probe.record(
        "unlock_status", workspace.client.get(f"/imports/{statement['id']}").json()
    )

    privacy_probe.assert_clean(PASSWORD, PAYEE, ACCOUNT_NUMBER)


def test_reconciliation_failure_responses_carry_no_amounts(workspace, privacy_probe):
    account = workspace.account(opening_balance="0")
    statement = workspace.stage(
        [(__import__("datetime").date(2026, 4, 1), f"UPI {PAYEE}", "-4321.55", "")],
        opening="100000.00",
        closing="95000.00",
    )

    blocked = workspace.confirm(statement, account)
    assert blocked.status_code == 409
    privacy_probe.record_response("difference_blocked", blocked)
    mismatch = workspace.client.post(
        f"/imports/{statement.id}/confirm", json={"account_id": account.id + 99}
    )
    privacy_probe.record_response("wrong_account", mismatch)

    privacy_probe.assert_clean(*SECRETS, "5000", "95000", "100000")


def test_unexpected_server_error_is_contained_and_logged_without_its_message(
    workspace, privacy_probe, monkeypatch
):
    def explode(path, password=None):
        raise ValueError(f"could not read row for {PAYEE} {NAME} {AMOUNT} {ACCOUNT_NUMBER}")

    monkeypatch.setattr("app.imports.parse_statement", explode)

    # raise_server_exceptions=True: an escaped exception here is what a server would log in full.
    response = _upload(workspace, STATEMENT)

    assert response.status_code == 500
    assert response.json() == {"detail": "Something went wrong. Nothing was changed."}
    privacy_probe.record_response("server_error", response)
    privacy_probe.assert_clean(*SECRETS)
    assert any("ValueError" in record.getMessage() for record in privacy_probe._caplog.records)
