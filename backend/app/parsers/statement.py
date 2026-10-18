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
        transactions.append(
            ParsedTransaction(
                txn_date=txn_date,
                description=" ".join(row[description_col].split()),
                amount=amount,
                balance=balance,
                reference=reference,
            )
        )
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


def _parse_yes_credit_card(text: str) -> ParsedStatement:
    details = text.split("Statement Details", 1)[-1]
    starts = list(re.finditer(r"(?m)^(\d{2}/\d{2}/\d{4})\s+", details))
    transactions = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(details)
        block = details[match.end() : end]
        amounts = list(re.finditer(r"([\d,]+\.\d{2})\s+(Dr|Cr)\b", block, re.I))
        if not amounts:
            continue
        amount_match = amounts[-1]
        description = " ".join(block[: amount_match.start()].split())
        if not description:
            continue
        reference_match = re.search(r"Ref No:\s*([A-Z0-9-]+)", description, re.I)
        transactions.append(
            ParsedTransaction(
                txn_date=parse_date(match.group(1)),
                description=description,
                amount=parse_amount(f"{amount_match.group(1)} {amount_match.group(2)}"),
                reference=reference_match.group(1) if reference_match else "",
            )
        )
    if not transactions:
        raise ParseError("No credit-card transaction rows could be read from this statement.")
    period = re.search(
        r"Statement Period:\s*.*?(\d{2}/\d{2}/\d{4})\s+To\s+(\d{2}/\d{2}/\d{4})",
        text,
        re.S,
    )
    account = re.search(r"Card Number\s+([0-9Xx]+)", text)
    previous = re.search(r"Previous Balance\s*:\s*Rs\.\s*([\d,]+\.\d{2})\s*Dr", text, re.S)
    total_due = re.search(r"Total Amount Due:.*?Rs\.\s*([\d,]+\.\d{2})", text, re.S)
    return ParsedStatement(
        transactions=transactions,
        bank_slug="yes",
        source_hint=account.group(1)[-8:] if account else "",
        statement_kind="credit_card",
        period_start=parse_date(period.group(1)) if period else transactions[0].txn_date,
        period_end=parse_date(period.group(2)) if period else transactions[-1].txn_date,
        opening_balance=-parse_amount(previous.group(1)) if previous else None,
        closing_balance=-parse_amount(total_due.group(1)) if total_due else None,
    )


def parse_statement(path: Path, password: str | None = None) -> ParsedStatement:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            with pdfplumber.open(path, password=password) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                tables = [table for page in pdf.pages for table in (page.extract_tables() or [])]
        except Exception as exc:
            message = f"{type(exc).__name__} {exc!s} {type(exc.__context__).__name__}".lower()
            if "password" in message or "decrypt" in message or "pdfminerexception" in message:
                raise ParseError("This PDF is password protected.") from exc
            raise ParseError("The PDF could not be opened.") from exc
        if "yes bank" in text.lower() and "credit card statement" in text.lower():
            return _parse_yes_credit_card(text)
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
