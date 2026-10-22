"""PNB ONE account statement: Date | Instrument ID | Amount | Type | Balance |
Remarks, where Type is DR or CR. Pure function: canonical rows out, no database writes.

Direction comes from the Type column, never from the sign of the amount, and the running balance
is carried into every row so the statement can be checked against its own balances.
"""

import re
from decimal import Decimal

from app.parsers.errors import ParseError
from app.parsers.normalise import parse_amount, parse_date
from app.parsers.types import ParsedStatement, ParsedTransaction

_PERIOD = re.compile(r"(\d{2}-\d{2}-\d{4})\s*(?:to|-)\s*(\d{2}-\d{2}-\d{4})", re.I)
_ACCOUNT = re.compile(r":\s*(\d{12,18})\b")


def matches(text: str) -> bool:
    lowered = text.lower()
    return (
        re.search(r"\bpnb\b|punjab national", lowered) is not None
        and "statement of account" in lowered
        and "instrument id" in lowered
    )


def _column(headers: list[str], *names: str) -> int | None:
    normalised = [re.sub(r"[^a-z]", "", header.lower()) for header in headers]
    for name in names:
        for index, header in enumerate(normalised):
            if name in header:
                return index
    return None


def parse_statement(text: str, rows: list[list[str]]) -> ParsedStatement:
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if _column(row, "date") is not None
            and _column(row, "type") is not None
            and _column(row, "balance") is not None
        ),
        None,
    )
    if header_index is None:
        raise ParseError("We could not find the transaction table header.")
    headers = rows[header_index]
    date_col = _column(headers, "date")
    amount_col = _column(headers, "amount")
    type_col = _column(headers, "type")
    balance_col = _column(headers, "balance")
    remarks_col = _column(headers, "remarks", "narration", "particulars")
    reference_col = _column(headers, "instrumentid", "reference")
    if None in (date_col, amount_col, type_col, balance_col):
        raise ParseError("The statement layout is not supported yet.")

    transactions: list[ParsedTransaction] = []
    for row in rows[header_index + 1 :]:
        if len(row) <= max(date_col, amount_col, type_col, balance_col):
            continue
        if not re.match(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$", row[date_col].strip()):
            continue  # repeated header or a footer line, not a movement
        direction = row[type_col].strip().upper()
        if direction not in {"DR", "CR"}:
            # Never guess a direction: a row without a clear marker must stop the import.
            raise ParseError("A row had no clear debit or credit marker, so nothing was imported.")
        try:
            amount = abs(parse_amount(row[amount_col]))
            balance = parse_amount(row[balance_col])
            txn_date = parse_date(row[date_col])
        except ValueError as exc:
            raise ParseError("A row could not be read, so nothing was imported.") from exc
        transactions.append(
            ParsedTransaction(
                txn_date=txn_date,
                description=" ".join(row[remarks_col].split()) if remarks_col is not None else "",
                amount=-amount if direction == "DR" else amount,
                balance=balance,
                reference=(
                    " ".join(row[reference_col].split()) if reference_col is not None else ""
                ),
            )
        )
    if not transactions:
        raise ParseError("No transaction rows could be read from this statement.")
    if transactions[0].txn_date > transactions[-1].txn_date:
        transactions.reverse()  # newest-first listing: restore chronological order
    first, last = transactions[0], transactions[-1]
    period = _PERIOD.search(text)
    account = _ACCOUNT.search(text)
    return ParsedStatement(
        transactions=transactions,
        bank_slug="pnb",
        account_name="PNB account",
        source_hint=account.group(1)[-8:] if account else "",
        statement_kind="bank",
        instrument_confidence="unknown",
        parser_name="pnb_table",
        period_start=(parse_date(period.group(1).replace("-", "/")) if period else first.txn_date),
        period_end=(parse_date(period.group(2).replace("-", "/")) if period else last.txn_date),
        opening_balance=first.balance - first.amount if first.balance is not None else None,
        closing_balance=last.balance,
    )


_ = Decimal
