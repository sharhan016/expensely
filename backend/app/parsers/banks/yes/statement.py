import re
from dataclasses import replace

from app.card_semantics import classify_card_transaction
from app.parsers.errors import ParseError
from app.parsers.normalise import parse_amount, parse_date
from app.parsers.table import parse_table
from app.parsers.types import ParsedStatement, ParsedTransaction

_YES_BANK = re.compile(r"\byes\s+bank\b", re.I)
_CREDIT_CARD = re.compile(r"\bcredit\s+card\s+statement\b", re.I)
_ACCOUNT_NUMBER = re.compile(
    r"(?:Account|A/C)\s*(?:Number|No\.?)\s*:?\s*([0-9Xx*\s-]{6,})",
    re.I,
)
_EXPLICIT_ACCOUNT_TYPE = re.compile(
    r"(?:Account|A/C)\s*Type\s*:?\s*(Current|Savings?)\b",
    re.I,
)


def matches(text: str) -> bool:
    return _YES_BANK.search(text) is not None


def _masked_hint(text: str, pattern: re.Pattern[str] = _ACCOUNT_NUMBER) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    return re.sub(r"[\s-]+", "", match.group(1))[-8:]


def _deposit_instrument(text: str) -> tuple[str, str]:
    explicit = _EXPLICIT_ACCOUNT_TYPE.search(text)
    if explicit:
        instrument = explicit.group(1).casefold()
        return ("savings" if instrument.startswith("saving") else "current", "high")
    if re.search(r"\bcurrent\s+account\b", text, re.I):
        return "current", "high"
    if re.search(r"\bsavings?\s+account\b", text, re.I):
        return "savings", "high"
    return "bank", "unknown"


def parse_credit_card(text: str) -> ParsedStatement:
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
        amount = parse_amount(f"{amount_match.group(1)} {amount_match.group(2)}")
        semantic = classify_card_transaction(description, amount)
        transactions.append(
            ParsedTransaction(
                txn_date=parse_date(match.group(1)),
                description=description,
                amount=amount,
                reference=reference_match.group(1) if reference_match else "",
                category=semantic.category,
                category_confidence=semantic.confidence,
                classification_evidence=semantic.evidence,
            )
        )
    if not transactions:
        raise ParseError("No credit-card transaction rows could be read from this statement.")
    period = re.search(
        r"Statement Period:\s*.*?(\d{2}/\d{2}/\d{4})\s+To\s+(\d{2}/\d{2}/\d{4})",
        text,
        re.S,
    )
    card_number = re.compile(r"Card Number\s+([0-9Xx*\s-]{8,})", re.I)
    previous = re.search(r"Previous Balance\s*:\s*Rs\.\s*([\d,]+\.\d{2})\s*Dr", text, re.S)
    total_due = re.search(r"Total Amount Due:.*?Rs\.\s*([\d,]+\.\d{2})", text, re.S)
    return ParsedStatement(
        transactions=transactions,
        bank_slug="yes",
        account_name="YES BANK Uni RuPay Credit Card",
        source_hint=_masked_hint(text, card_number),
        statement_kind="credit_card",
        instrument_confidence="high",
        parser_name="yes_credit_card",
        parser_version="1",
        period_start=parse_date(period.group(1)) if period else transactions[0].txn_date,
        period_end=parse_date(period.group(2)) if period else transactions[-1].txn_date,
        opening_balance=-parse_amount(previous.group(1)) if previous else None,
        closing_balance=-parse_amount(total_due.group(1)) if total_due else None,
    )


def parse_statement(text: str, rows: list[list[str]]) -> ParsedStatement:
    if _CREDIT_CARD.search(text):
        return parse_credit_card(text)
    parsed = parse_table(rows, "yes")
    instrument, confidence = _deposit_instrument(text)
    label = {
        "current": "YES BANK Current Account",
        "savings": "YES BANK Savings Account",
        "bank": "YES BANK Account",
    }[instrument]
    return replace(
        parsed,
        account_name=label,
        source_hint=_masked_hint(text),
        statement_kind=instrument,
        instrument_confidence=confidence,
        parser_name="yes_deposit_table",
        parser_version="1",
    )
