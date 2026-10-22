import csv
import io
import logging
import re
from dataclasses import replace
from pathlib import Path

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

from app.parsers.banks.yes import parse_credit_card
from app.parsers.errors import ParseError
from app.parsers.registry import parse_known_statement
from app.parsers.table import parse_table
from app.parsers.types import ParsedStatement

# At DEBUG these libraries log the text they extract (narrations, amounts, names). Pin them so a
# verbose root logger can never write statement contents to a log.
for _noisy in ("pdfminer", "pdfplumber", "PIL", "pypdfium2"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def _detect_bank(text: str) -> str:
    lowered = text.lower()
    if "www.federalbank.co.in" in lowered or "federalbank.co.in" in lowered:
        return "federal"
    for needle, slug in (
        ("hdfc bank", "hdfc"),
        ("icici bank", "icici"),
        ("state bank of india", "sbi"),
        ("axis bank", "axis"),
        ("kotak mahindra", "kotak"),
        ("yes bank", "yes"),
    ):
        if needle in lowered:
            return slug
    return "unknown"


def _add_account_identity(parsed: ParsedStatement, text: str) -> ParsedStatement:
    if parsed.bank_slug != "federal":
        return parsed
    account = re.search(
        r"Account\s+(?:Number|No\.?)\s*:\s*([0-9Xx*\s]{6,})",
        text,
        re.I,
    )
    compact = re.sub(r"\s+", "", account.group(1)) if account else ""
    return replace(
        parsed,
        account_name="Federal Bank Account",
        source_hint=compact[-8:],
        parser_name="federal_table",
    )


def _parse_yes_credit_card(text: str) -> ParsedStatement:
    """Compatibility wrapper for focused parser tests and callers."""
    return parse_credit_card(text)


def pdf_open_error(exc: BaseException) -> ParseError:
    """Ask for a password only when the PDF library says the password is the problem."""
    causes = [exc, exc.__cause__, exc.__context__, *exc.args]
    if any(isinstance(cause, PDFPasswordIncorrect) for cause in causes):
        return ParseError("This PDF is password protected.")
    return ParseError(
        "We could not read this PDF. It may be damaged, incomplete or use unsupported "
        "protection; try downloading it again."
    )


def parse_statement(path: Path, password: str | None = None) -> ParsedStatement:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            with pdfplumber.open(path, password=password) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                tables = [table for page in pdf.pages for table in (page.extract_tables() or [])]
        except Exception as exc:
            raise pdf_open_error(exc) from exc
        rows = [row for table in tables for row in table]
        clean_rows = [[cell or "" for cell in row] for row in rows]
        known = parse_known_statement(text, clean_rows)
        if known:
            return known
        parsed = parse_table(clean_rows, _detect_bank(text))
        return _add_account_identity(parsed, text)
    if suffix in {".csv", ".txt"}:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))
        known = parse_known_statement(text, rows)
        if known:
            return known
        parsed = parse_table(rows, _detect_bank(text))
        return _add_account_identity(parsed, text)
    raise ParseError("Use a PDF, CSV, or text statement for this MVP.")
