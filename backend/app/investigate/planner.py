"""Rule-based planner and answer templates. No model: tools are picked by fixed rules and every
sentence is a template over tool results, so each claim points at records that exist."""

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cash import _rupees
from app.coverage import date_label
from app.investigate import tools
from app.models import Transaction
from app.period_report import _MONTHS, MAX_SPAN_DAYS, parse_range

logger = logging.getLogger("expense.investigate")

FACT_KINDS = {"exact", "possible", "fact", "gap", "close", "none"}
TOOL_LABELS = {
    "find_by_amount": "amount matches",
    "find_cash_withdrawals": "cash withdrawals",
    "balance_discontinuities": "balance connections",
    "coverage_gaps": "coverage",
    "unresolved_in_window": "unresolved movements",
}
NOTE = "A match is a record, not an explanation. I cannot tell from the records why money moved."
ASK_AMOUNT = "Which amount are you looking for? For example: where did my 1200 go."
ASK_WHEN = "Roughly when did it happen? Even a month helps."
ASK_ACCOUNT = "Which account was it on?"

_MONTH_WORDS = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE_TEXT = re.compile(
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH_WORDS})\b(?:\s+\d{{4}})?|"
    rf"\b(?:{_MONTH_WORDS})\s+\d{{4}}\b|\b\d{{1,2}}/\d{{1,2}}/\d{{4}}\b",
    re.I,
)
_NUMBER = re.compile(r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)", re.I)
INTENT = re.compile(
    r"where did|where has|where have|went|missing|can't find|cannot find|can not find|"
    r"unexplained|find",
    re.I,
)


def extract_amount(message: str) -> Decimal | None:
    """The first amount in the message, or None. More than two decimals is not rounded."""
    cleaned = _DATE_TEXT.sub(" ", message)
    match = _NUMBER.search(cleaned)
    if not match:
        return None
    try:
        value = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    if value <= 0 or -value.as_tuple().exponent > 2 or value >= Decimal("1000000000000"):
        return None
    return value.quantize(Decimal("0.01"))


def wants_investigation(message: str) -> bool:
    return bool(INTENT.search(message)) and bool(_NUMBER.search(_DATE_TEXT.sub(" ", message)))


def _ledger_window(db: Session, user_id: int) -> tuple[date, date] | None:
    low, high = db.execute(
        select(func.min(Transaction.txn_date), func.max(Transaction.txn_date)).where(
            Transaction.user_id == user_id
        )
    ).one()
    if low is None:
        return None
    if (high - low).days >= MAX_SPAN_DAYS:
        low = date.fromordinal(high.toordinal() - (MAX_SPAN_DAYS - 1))
    return low, high


def _span(start: date, end: date) -> str:
    return date_label(start) if start == end else f"{date_label(start)} to {date_label(end)}"


class _Answer:
    def __init__(self):
        self.claims: list[dict] = []
        self.evidence: dict[str, dict] = {}

    def add(self, kind: str, text: str, refs: list[tuple[str, str]] | None = None) -> None:
        refs = refs or []
        for ref, label in refs:
            self.evidence.setdefault(ref, {"ref": ref, "label": label, "type": ref.split(":")[0]})
        self.claims.append({"kind": kind, "text": text, "evidence": [ref for ref, _ in refs]})


def _txn_label(item: dict) -> str:
    verb = "Money out" if item["direction"] == "out" else "Money in"
    day = date_label(date.fromisoformat(item["txn_date"]))
    return f"{verb} of {_rupees(abs(Decimal(item['amount'])))} on {day}"


def _run(name: str, db: Session, user_id: int, payload: dict, failures: list[str]):
    model, function = tools.TOOLS[name]
    try:
        return function(db, user_id, model.model_validate(payload))
    except Exception as exc:  # a failing check must never become a conclusion
        logger.warning("tool %s failed: %s", name, type(exc).__name__)  # class only, no message
        failures.append(name)
        return None


def investigate(db: Session, user_id: int, message: str, *, today: date | None = None) -> dict:
    today = today or date.today()
    amount = extract_amount(message)
    answer = _Answer()
    if amount is None:
        answer.add("question", ASK_AMOUNT)
        return _finish(answer, None, None, ASK_AMOUNT, [])

    explicit = parse_range(message, today=today)
    window = (explicit.start, explicit.end) if explicit else _ledger_window(db, user_id)
    if window and (window[1] - window[0]).days >= MAX_SPAN_DAYS:
        window = (date.fromordinal(window[1].toordinal() - (MAX_SPAN_DAYS - 1)), window[1])
        explicit = None
    close = bool(re.search(r"\b(close|near|nearly|around|approx\w*)\b", message, re.I))
    money = _rupees(amount)
    answer.add(
        "note",
        f"Looking for {money} between {date_label(window[0])} and {date_label(window[1])}."
        if window
        else f"Looking for {money} across all the records I have.",
    )
    win = {"date_from": window[0], "date_to": window[1]} if window else {}
    failures: list[str] = []
    ran = ["find_by_amount", "find_cash_withdrawals"]
    names = tools._names(db, user_id)
    del names

    found = _run(
        "find_by_amount", db, user_id, {"amount": amount, "include_close": close, **win}, failures
    )
    exact = [m for m in (found or {"matches": []})["matches"] if m["kind"] == "exact"]
    near = [m for m in (found or {"matches": []})["matches"] if m["kind"] == "close"]
    matched = False

    if exact:
        matched = True
        listed = "; ".join(
            f"money {m['direction']} on {date_label(date.fromisoformat(m['txn_date']))}"
            for m in exact[:5]
        )
        more = f" and {len(exact) - 5} more" if len(exact) > 5 else ""
        count = f"{len(exact)} movement{'s' if len(exact) != 1 else ''}"
        answer.add(
            "exact",
            f"I found {count} of exactly {money}: {listed}{more}.",
            [(m["evidence"], _txn_label(m)) for m in exact],
        )
    for m in near[:5]:
        matched = True
        verb = "money out of" if m["direction"] == "out" else "money in of"
        answer.add(
            "close",
            f"Close, not exact: {verb} {_rupees(abs(Decimal(m['amount'])))} on "
            f"{date_label(date.fromisoformat(m['txn_date']))}.",
            [(m["evidence"], _txn_label(m))],
        )

    cash = _run("find_cash_withdrawals", db, user_id, {"amount": amount, **win}, failures)
    exact_ids = {m["transaction_id"] for m in exact}
    if "find_cash_withdrawals" in failures:
        answer.add("error", _error_text("find_cash_withdrawals"))
    for combo in (cash or {"combinations": []})["combinations"]:
        if len(combo["transaction_ids"]) == 1 and combo["transaction_ids"][0] in exact_ids:
            continue
        if sum(1 for c in answer.claims if c["kind"] == "possible") >= 3:
            break
        matched = True
        parts = [
            f"{_rupees(Decimal(a))} on {date_label(date.fromisoformat(d))}"
            for a, d in zip(combo["amounts"], combo["dates"], strict=True)
        ]
        if len(parts) == 1:
            text = f"Possible, not a cause: one ATM withdrawal of {parts[0]}."
        else:
            joined = (
                " and ".join([", ".join(parts[:-1]), parts[-1]])
                if len(parts) > 2
                else (f"{parts[0]} and {parts[1]}")
            )
            text = f"Possible, not a cause: ATM withdrawals of {joined} add up to {money}."
        answer.add(
            "possible",
            text,
            [
                (ref, f"ATM withdrawal of ₹{a}")
                for ref, a in zip(combo["evidence"], combo["amounts"], strict=True)
            ],
        )

    gap_claims = False
    if window:
        ran += ["balance_discontinuities", "coverage_gaps"]
        disc = _run("balance_discontinuities", db, user_id, {"amount": amount, **win}, failures)
        for item in (disc or {"discontinuities": []})["discontinuities"]:
            first, last = date.fromisoformat(item["after"]), date.fromisoformat(item["before"])
            refs = [
                (item["evidence"][0], f"Balances do not connect: {_span(first, last)}"),
                (item["evidence"][1], "Statement before the break"),
                (item["evidence"][2], "Statement after the break"),
            ]
            if item["matches_amount"]:
                matched = True
                answer.add(
                    "fact",
                    f"The statements do not connect by exactly {money} between "
                    f"{date_label(first)} and {date_label(last)}. That is a fact about the "
                    "records, not a cause.",
                    refs,
                )
            else:
                gap_claims = True
                answer.add(
                    "gap",
                    f"The balances also do not connect between {date_label(first)} and "
                    f"{date_label(last)} (difference {_rupees(Decimal(item['difference']))}).",
                    refs,
                )
        if "balance_discontinuities" in failures:
            answer.add("error", _error_text("balance_discontinuities"))
        gaps = _run("coverage_gaps", db, user_id, win, failures)
        seen: set[tuple[str, str]] = set()
        for item in (gaps or {"gaps": []})["gaps"]:
            key = (item["start"], item["end"])
            if key in seen or len(seen) >= 3:
                continue
            seen.add(key)
            gap_claims = True
            span = _span(date.fromisoformat(item["start"]), date.fromisoformat(item["end"]))
            answer.add(
                "gap",
                f"Nothing covers {span}, so I cannot see what happened on those dates.",
                [(item["evidence"], f"No checked statement for {span}")],
            )
        if "coverage_gaps" in failures:
            answer.add("error", _error_text("coverage_gaps"))

    if not matched and not failures:
        scope = f"scope:{window[0].isoformat()}:{window[1].isoformat()}" if window else "scope:all"
        answer.add(
            "none",
            f"I found nothing in my records that matches {money}.",
            [(scope, "The dates I searched")],
        )
        if window and not gap_claims:
            ran.append("unresolved_in_window")
            unresolved = _run("unresolved_in_window", db, user_id, win, failures)
            if unresolved and unresolved["total"]:
                n = unresolved["total"]
                answer.add(
                    "fact",
                    f"{n} movement{'s' if n != 1 else ''} in this window still "
                    f"{'have' if n != 1 else 'has'} no purpose, so I cannot say what "
                    f"{'they were' if n != 1 else 'it was'} for.",
                    [(m["evidence"], _txn_label(m)) for m in unresolved["items"]],
                )
    if matched:
        answer.add("note", NOTE)

    question = None
    if not matched:
        question = ASK_ACCOUNT if explicit else ASK_WHEN
        answer.add("question", question)
    return _finish(answer, amount, window, question, ran, explicit=bool(explicit))


def _error_text(name: str) -> str:
    return (
        f"I could not run one of the checks ({TOOL_LABELS[name]}). I have not drawn any "
        "conclusion from it."
    )


def _finish(answer: _Answer, amount, window, question, ran, explicit=False) -> dict:
    # Rule: a factual claim without evidence never leaves this function.
    answer.claims = [c for c in answer.claims if c["kind"] not in FACT_KINDS or c["evidence"]]
    return {
        "amount": f"{amount:.2f}" if amount is not None else None,
        "window": (
            {"start": window[0].isoformat(), "end": window[1].isoformat(), "explicit": explicit}
            if window
            else None
        ),
        "claims": answer.claims,
        "evidence": list(answer.evidence.values()),
        "question": question,
        "tools_run": ran,
        "content": "\n".join(c["text"] for c in answer.claims),
    }
