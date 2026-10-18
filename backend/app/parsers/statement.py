import contextlib
import csv
import io
import re
from pathlib import Path

import pdfplumber

from app.parsers.normalise import parse_amount, parse_date, parse_signed_amount
from app.parsers.types import ParsedStatement, ParsedTransaction


class ParseError(Exception):
    pass


def _detect_bank(text: str) -> str:
    lowered = text.lower()
    for needle, slug in (
        ("hdfc bank", "hdfc"), ("icici bank", "icici"), ("state bank of india", "sbi"),
        ("axis bank", "axis"), ("kotak mahindra", "kotak"), ("yes bank", "yes"),
    ):
        if needle in lowered:
            return slug
    return "unknown"


def _column(headers: list[str], *names: str) -> int | None:
    normalised = [re.sub(r"[^a-z]", "", header.lower()) for header in headers]
    for name in names:
        key = re.sub(r"[^a-z]", "", name.lower())
        for index, header in enumerate(normalised):
            if key in header:
                return index
    return None


def _parse_table(rows: list[list[str]], bank_slug: str) -> ParsedStatement:
    header_index = next(
        (i for i, row in enumerate(rows) if any("date" in cell.lower() for cell in row)), None
    )
    if header_index is None:
        raise ParseError("We could not find the transaction table header.")
    headers = rows[header_index]
    date_col = _column(headers, "transaction date", "txn date", "date")
    description_col = _column(headers, "narration", "description", "particulars", "remarks")
    debit_col = _column(headers, "withdrawal", "debit")
    credit_col = _column(headers, "deposit", "credit")
    amount_col = _column(headers, "amount")
    balance_col = _column(headers, "closing balance", "balance")
    reference_col = _column(headers, "reference", "chq", "utr")
    if date_col is None or description_col is None or (amount_col is None and debit_col is None):
        raise ParseError(
            "The statement layout is not supported yet. Exporting CSV may work better."
        )
    transactions = []
    for row in rows[header_index + 1 :]:
        if len(row) <= max(date_col, description_col):
            continue
        try:
            txn_date = parse_date(row[date_col])
            amount = (
                parse_amount(row[amount_col])
                if amount_col is not None
                else parse_signed_amount(
                    row[debit_col] if debit_col is not None and debit_col < len(row) else "",
                    row[credit_col] if credit_col is not None and credit_col < len(row) else "",
                )
            )
        except (ValueError, IndexError):
            continue
        balance = None
        if balance_col is not None and balance_col < len(row) and row[balance_col].strip():
            with contextlib.suppress(ValueError):
                balance = parse_amount(row[balance_col])
        reference = ""
        if reference_col is not None and reference_col < len(row):
            reference = row[reference_col].strip()
        transactions.append(ParsedTransaction(
            txn_date=txn_date,
            description=" ".join(row[description_col].split()),
            amount=amount,
            balance=balance,
            reference=reference,
        ))
    if not transactions:
        raise ParseError("No transaction rows could be read from this statement.")
    transactions.sort(key=lambda transaction: transaction.txn_date)
    return ParsedStatement(
        transactions=transactions,
        bank_slug=bank_slug,
        period_start=transactions[0].txn_date,
        period_end=transactions[-1].txn_date,
        closing_balance=transactions[-1].balance,
    )


def parse_statement(path: Path, password: str | None = None) -> ParsedStatement:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            with pdfplumber.open(path, password=password) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                tables = [table for page in pdf.pages for table in (page.extract_tables() or [])]
        except Exception as exc:
            message = str(exc).lower()
            if "password" in message or "decrypt" in message:
                raise ParseError("This PDF is password protected.") from exc
            raise ParseError("The PDF could not be opened.") from exc
        rows = [row for table in tables for row in table]
        return _parse_table([[cell or "" for cell in row] for row in rows], _detect_bank(text))
    if suffix in {".csv", ".txt"}:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return _parse_table(list(csv.reader(io.StringIO(text), dialect)), _detect_bank(text))
    raise ParseError("Use a PDF, CSV, or text statement for this MVP.")
