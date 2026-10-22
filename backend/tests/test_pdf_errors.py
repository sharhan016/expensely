import pytest
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError
from pdfplumber.utils.exceptions import PdfminerException

from app.config import settings
from app.parsers.errors import ParseError
from app.parsers.statement import pdf_open_error


@pytest.fixture(autouse=True)
def private_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")


def _upload(workspace, body: bytes):
    return workspace.client.post(
        "/imports", files=[("files", ("statement.pdf", body, "application/pdf"))]
    )


def test_real_password_problem_asks_for_a_password():
    error = pdf_open_error(PdfminerException(PDFPasswordIncorrect()))

    assert isinstance(error, ParseError)
    assert "password" in str(error).lower()


@pytest.mark.parametrize(
    "cause", [PDFSyntaxError("No /Root object"), PDFEncryptionError("unsupported"), ValueError("x")]
)
def test_unreadable_pdf_is_not_reported_as_a_password_problem(cause):
    error = pdf_open_error(PdfminerException(cause))

    assert "password" not in str(error).lower()
    assert "could not read" in str(error).lower()


def test_corrupt_upload_gets_its_own_message_and_never_asks_for_a_password(workspace):
    response = _upload(workspace, b"%PDF-1.4 this is not a real document")

    statement = response.json()[0]
    assert statement["status"] == "failed"
    assert "password" not in statement["error_message"].lower()
    assert "could not read" in statement["error_message"].lower()
