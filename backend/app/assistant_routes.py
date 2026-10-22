import re
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.balances import account_balances, overall_context
from app.cash import cash_summary
from app.coverage import date_label
from app.coverage_service import coverage_sentence
from app.database import get_db
from app.investigate.planner import investigate, wants_investigation
from app.models import (
    Account,
    AssistantMessage,
    Counterparty,
    ImportStatus,
    Purpose,
    StatementImport,
    Transaction,
    TransactionLink,
    TransactionMeaning,
    User,
)
from app.money_in_motion import outstanding, pending_questions, summary_sentences
from app.period_report import build_report, parse_range
from app.schemas import AssistantMessageResponse, AssistantTurnRequest
from app.security import current_user
from app.services import calculate_reconciliation, legacy_unconfirmed_count

router = APIRouter(prefix="/assistant", tags=["private assistant"])


def _money(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    whole, fraction = f"{abs(value):.2f}".split(".")
    if len(whole) > 3:
        tail = whole[-3:]
        head = whole[:-3]
        groups = []
        while head:
            groups.append(head[-2:])
            head = head[:-2]
        whole = f"{','.join(reversed(groups))},{tail}"
    return f"{sign}₹{whole}.{fraction}"


def _date_label(iso: str | None) -> str:
    if not iso:
        return ""
    parsed = date.fromisoformat(iso)
    return f"{parsed.day} {parsed.strftime('%b %Y')}"


def _import_reply(statements: list[StatementImport]) -> tuple[str, dict]:
    lines = []
    needs_review = False
    reconciliation_results = []
    for statement in statements:
        institution = (
            statement.bank_slug.upper() if statement.bank_slug != "unknown" else "Unknown bank"
        )
        instrument = {
            "credit_card": "credit-card statement",
            "current": "current-account statement",
            "savings": "savings-account statement",
            "bank": "bank statement",
        }.get(statement.statement_kind, "statement")
        period = (
            f" for {date_label(statement.period_start)} to {date_label(statement.period_end)}"
            if statement.period_start and statement.period_end
            else ""
        )
        if statement.status == ImportStatus.READY:
            needs_review = True
            transaction_count = len(statement.rows)
            selected = [row for row in statement.rows if row.selected]
            duplicate_count = sum(row.duplicate_of_id is not None for row in selected)
            proposed = [row for row in selected if row.duplicate_of_id is None]
            unresolved_count = sum(row.purpose == Purpose.OTHER for row in proposed)
            outflow = -sum((row.amount for row in proposed if row.amount < 0), Decimal("0"))
            inflow = sum((row.amount for row in proposed if row.amount > 0), Decimal("0"))
            if statement.opening_balance is not None and statement.closing_balance is not None:
                _, difference = calculate_reconciliation(
                    statement.opening_balance,
                    [row.amount for row in selected],
                    statement.closing_balance,
                )
                reconciliation = "balanced" if difference == 0 else "difference"
                reconciliation_copy = (
                    "Its opening balance, movements, and closing balance reconcile."
                    if difference == 0
                    else (
                        f"Its balance check has a {_money(abs(difference))} difference that needs "
                        "review."
                    )
                )
            else:
                reconciliation = "unavailable"
                reconciliation_copy = (
                    "This layout does not expose both balance endpoints, so its balance check "
                    "is still pending."
                )
            flow_totals: dict[str, tuple[int, Decimal]] = {}
            for row in proposed:
                if not row.counterparty_name:
                    continue
                count, total = flow_totals.get(row.counterparty_name, (0, Decimal("0")))
                flow_totals[row.counterparty_name] = (count + 1, total + abs(row.amount))
            repeated = [
                f"{name}: {count} movements totaling {_money(total)}"
                for name, (count, total) in flow_totals.items()
                if count > 1
            ]
            card_categories: dict[str, int] = {}
            if statement.statement_kind == "credit_card":
                for row in proposed:
                    card_categories[row.category] = card_categories.get(row.category, 0) + 1
            lines.append(
                f"I found a {institution} {instrument}{period} with {transaction_count} "
                f"transaction{'s' if transaction_count != 1 else ''}. {reconciliation_copy} "
                f"Before posting, I see {len(proposed)} new movement"
                f"{'s' if len(proposed) != 1 else ''}, {duplicate_count} proven duplicate"
                f"{'s' if duplicate_count != 1 else ''}, and {unresolved_count} meaning"
                f"{'s' if unresolved_count != 1 else ''} still unresolved. Money moving out is "
                f"{_money(outflow)} and money moving in is {_money(inflow)}; I will not call "
                "either spending or income until the purpose is known."
            )
            if repeated:
                lines.append(f"Repeated flows found: {'; '.join(repeated)}.")
            if card_categories:
                category_copy = "; ".join(
                    f"{count} {category.replace('_', ' ')}"
                    for category, count in sorted(card_categories.items())
                )
                lines.append(f"Card activity identified: {category_copy}.")
            if statement.statement_kind == "credit_card":
                card_amounts = []
                if statement.total_amount_due is not None:
                    card_amounts.append(f"total due {_money(statement.total_amount_due)}")
                if statement.minimum_amount_due is not None:
                    card_amounts.append(f"minimum due {_money(statement.minimum_amount_due)}")
                if statement.available_credit is not None:
                    card_amounts.append(f"available credit {_money(statement.available_credit)}")
                if statement.statement_balance is not None:
                    card_amounts.append(f"statement balance {_money(statement.statement_balance)}")
                if card_amounts:
                    lines.append(
                        f"The card document reports {', '.join(card_amounts)}; these values are "
                        "kept separate."
                    )
            lines.append("It is ready for your review before I add it to the ledger.")
            reconciliation_results.append(
                {"import_id": str(statement.id), "status": reconciliation}
            )
        elif statement.status == ImportStatus.PASSWORD_REQUIRED:
            lines.append(
                f"{statement.original_filename} needs its PDF password before I can read it."
            )
        elif statement.status == ImportStatus.FAILED:
            lines.append(
                f"I could not read {statement.original_filename}: {statement.error_message}"
            )
        else:
            lines.append(f"{statement.original_filename} is currently {statement.status.value}.")
    return "\n".join(lines), {
        "kind": "import_result",
        "import_ids": [str(statement.id) for statement in statements],
        "review_required": needs_review,
        "reconciliation": reconciliation_results,
    }


def _legacy_note(db: Session, user_id: int) -> str:
    count = legacy_unconfirmed_count(db, user_id)
    if not count:
        return ""
    if count == 1:
        return " 1 older entry is not used for suggestions until you confirm it."
    return f" {count} older entries are not used for suggestions until you confirm them."


def post_import_summary(db: Session, user_id: int, run) -> None:
    """Once a protected statement is unlocked, say what was found, once per run."""
    recent = db.scalars(
        select(AssistantMessage)
        .where(AssistantMessage.user_id == user_id, AssistantMessage.role == "assistant")
        .order_by(AssistantMessage.id.desc())
        .limit(50)
    ).all()
    if any(item.context.get("run_id") == str(run.id) for item in recent):
        return
    statements = db.scalars(
        select(StatementImport).where(
            StatementImport.user_id == user_id,
            StatementImport.id.in_([uuid.UUID(item) for item in run.import_ids]),
        )
    ).all()
    if not statements:
        return
    reply, context = _import_reply(statements)
    db.add(
        AssistantMessage(
            user_id=user_id,
            role="assistant",
            content=reply,
            context={**context, "run_id": str(run.id)},
        )
    )
    db.commit()


def _workspace_reply(db: Session, user_id: int, message: str) -> tuple[str, dict]:
    lowered = message.casefold()
    if any(term in lowered for term in ("reconcil", "covered", "coverage", "gap", "missing")):
        return coverage_sentence(db, user_id), {"kind": "coverage_summary"}
    if "cash" in lowered and "card" not in lowered:
        summary = cash_summary(db, user_id)
        db.commit()
        return f"{summary['summary']} {summary['note']}", {"kind": "cash_summary"}
    reply, context = _workspace_answer(db, user_id, message)
    if context.get("kind") == "workspace_summary":
        reply += f" {coverage_sentence(db, user_id)}"
        cash = cash_summary(db, user_id)
        db.commit()
        if cash["withdrawal_count"]:
            reply += f" {cash['summary']}"
    if context.get("kind") in {"workspace_summary", "unresolved_summary", "capabilities"}:
        reply += _legacy_note(db, user_id)
    return reply, context


def _workspace_answer(db: Session, user_id: int, message: str) -> tuple[str, dict]:
    lowered = message.casefold()
    transactions = db.scalars(select(Transaction).where(Transaction.user_id == user_id)).all()
    accounts = db.scalars(select(Account).where(Account.user_id == user_id)).all()
    if "unresolved" in lowered or "needs review" in lowered:
        unresolved = sum(transaction.purpose == Purpose.OTHER for transaction in transactions)
        return (
            f"You have {unresolved} confirmed ledger movement"
            f"{'s' if unresolved != 1 else ''} that still need a financial meaning.",
            {"kind": "unresolved_summary", "count": unresolved},
        )
    if "card" in lowered:
        card_ids = {account.id for account in accounts if account.instrument_type == "credit_card"}
        card_balances = [
            item for item in account_balances(db, user_id) if item.account_id in card_ids
        ]
        card_position = sum((item.balance for item in card_balances), Decimal("0"))
        card_as_of = _date_label(overall_context(card_balances)["as_of"])
        card_freshness = f" as of {card_as_of}" if card_as_of else " (no confirmed statement date)"
        linked = db.scalar(
            select(func.count(TransactionLink.id)).where(
                TransactionLink.user_id == user_id,
                TransactionLink.link_type == "card_payment",
                TransactionLink.status == "confirmed",
            )
        )
        linked_refunds = db.scalar(
            select(func.count(TransactionLink.id)).where(
                TransactionLink.user_id == user_id,
                TransactionLink.link_type.in_(("refund", "reversal")),
                TransactionLink.status == "confirmed",
            )
        )
        category_counts: dict[str, int] = {}
        for transaction in transactions:
            if transaction.account_id not in card_ids or not transaction.meaning:
                continue
            category = transaction.meaning.category
            category_counts[category] = category_counts.get(category, 0) + 1
        charges = sum(
            category_counts.get(category, 0)
            for category in ("fee", "interest", "tax", "emi_interest")
        )
        return (
            f"Your credit-card amount payable is {_money(max(-card_position, Decimal('0')))}"
            f"{card_freshness}. "
            f"I have linked {linked or 0} card payment{'s' if linked != 1 else ''} to bank debits "
            f"and {linked_refunds or 0} refund{'s' if linked_refunds != 1 else ''} to purchases. "
            f"{charges} fee, interest, or tax charge{'s' if charges != 1 else ''} are identified "
            "separately from transfers.",
            {
                "kind": "card_summary",
                "linked_card_payments": linked or 0,
                "linked_refunds": linked_refunds or 0,
                "charge_count": charges,
            },
        )
    if any(term in lowered for term in ("person", "shop", "merchant", "paid", "flow")):
        rows = db.execute(
            select(
                Counterparty.display_name,
                func.coalesce(
                    func.sum(case((Transaction.amount < 0, -Transaction.amount), else_=0)), 0
                ).label("paid"),
                func.count(Transaction.id).label("movement_count"),
            )
            .join(TransactionMeaning, TransactionMeaning.counterparty_id == Counterparty.id)
            .join(Transaction, Transaction.id == TransactionMeaning.transaction_id)
            .where(Counterparty.user_id == user_id, Transaction.user_id == user_id)
            .group_by(Counterparty.id)
            .order_by(func.sum(case((Transaction.amount < 0, -Transaction.amount), else_=0)).desc())
            .limit(5)
        ).all()
        if not rows:
            return (
                "I do not have any confirmed counterparty flows yet.",
                {"kind": "flow_summary", "count": 0},
            )
        details = "; ".join(
            f"{name}: {_money(paid)} across {count} movement{'s' if count != 1 else ''}"
            for name, paid, count in rows
        )
        return f"Your largest identified payment flows are {details}.", {
            "kind": "flow_summary",
            "count": len(rows),
        }
    if any(term in lowered for term in ("summary", "balance", "overview", "money")):
        bank_ids = {account.id for account in accounts if account.instrument_type != "credit_card"}
        balances = [item for item in account_balances(db, user_id) if item.account_id in bank_ids]
        bank_balance = sum((item.balance for item in balances), Decimal("0"))
        parts = []
        for item in balances:
            part = f"{item.name} {_money(item.balance)}"
            if item.as_of:
                part += f" as of {_date_label(item.as_of.isoformat())} ({item.age_days} days ago)"
            parts.append(part)
        different = len({item.as_of for item in balances if item.as_of}) > 1
        freshness = (
            "Your bank balances: "
            + "; ".join(parts)
            + f". Together that is {_money(bank_balance)}"
            + (", but they are from different dates" if different else "")
            + (
                ". Some of this is old, so it may not match your bank today"
                if any(item.is_stale for item in balances)
                else ""
            )
        )
        unresolved = sum(transaction.purpose == Purpose.OTHER for transaction in transactions)
        return (
            f"{freshness}. {unresolved} movement"
            f"{'s' if unresolved != 1 else ''} still need review.",
            {"kind": "workspace_summary", "unresolved_count": unresolved},
        )
    return (
        "I can read a statement, summarize balances, show unresolved movements, explain "
        "credit-card payments, or tell you who money moved to and from.",
        {"kind": "capabilities"},
    )


_MOTION_INTENT = re.compile(
    r"still out|expect\w* (it )?back|owe me|owes me|sent out|come back|came back", re.I
)


def _motion_reply(db: Session, user_id: int, message: str) -> tuple[str, dict] | None:
    if not _MOTION_INTENT.search(message):
        return None
    rows = outstanding(db, user_id)
    text, evidence = summary_sentences(rows)
    waiting = pending_questions(db, user_id)
    if waiting:
        text += (
            f" I am also waiting to hear whether {len(waiting)} "
            f"{'person' if len(waiting) == 1 else 'people'} you paid should be tracked as money "
            "expected back."
        )
    return text, {"kind": "motion_summary", "evidence": evidence}


def _investigate_reply(db: Session, user_id: int, message: str) -> tuple[str, dict] | None:
    """ "Where did my 1200 go" style questions, answered by the investigation tools."""
    if not wants_investigation(message):
        return None
    answer = investigate(db, user_id, message)
    context = {key: value for key, value in answer.items() if key != "content"}
    return answer["content"], {"kind": "investigation", **context}


def _report_reply(db: Session, user_id: int, message: str) -> tuple[str, dict] | None:
    """Answer "5 October to 20 October" style requests from the period report."""
    last = db.scalar(
        select(AssistantMessage)
        .where(AssistantMessage.user_id == user_id, AssistantMessage.role == "assistant")
        .order_by(AssistantMessage.id.desc())
        .limit(1)
    )
    pending = last.context if last and last.context.get("kind") == "range_confirm" else None
    if pending and message.strip().casefold().rstrip(".!") in {"yes", "y", "confirm", "ok", "okay"}:
        start, end = date.fromisoformat(pending["start"]), date.fromisoformat(pending["end"])
    else:
        parsed = parse_range(message, today=date.today())
        if parsed is None:
            return None
        if parsed.assumed:
            return (
                f"I read that as {date_label(parsed.start)} to {date_label(parsed.end)}. "
                "Say yes to continue, or tell me the dates in full.",
                {
                    "kind": "range_confirm",
                    "start": parsed.start.isoformat(),
                    "end": parsed.end.isoformat(),
                },
            )
        start, end = parsed.start, parsed.end
    report = build_report(db, user_id, start, end)
    return (
        f"{report['summary']} Open the Report page for the day-by-day table and to copy it.",
        {"kind": "period_report", "start": start.isoformat(), "end": end.isoformat()},
    )


@router.get("/messages", response_model=list[AssistantMessageResponse])
def messages(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return db.scalars(
        select(AssistantMessage)
        .where(AssistantMessage.user_id == user.id)
        .order_by(AssistantMessage.created_at, AssistantMessage.id)
        .limit(200)
    ).all()


@router.post("/turn", response_model=AssistantMessageResponse, status_code=201)
def assistant_turn(
    payload: AssistantTurnRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    message = payload.message.strip()
    if not message and not payload.import_ids:
        raise HTTPException(status_code=422, detail="Write a message or attach a statement")
    statements = []
    if payload.import_ids:
        statements = db.scalars(
            select(StatementImport).where(
                StatementImport.user_id == user.id,
                StatementImport.id.in_(payload.import_ids),
            )
        ).all()
        if len(statements) != len(set(payload.import_ids)):
            raise HTTPException(
                status_code=404, detail="One or more statement imports were not found"
            )
    user_message = AssistantMessage(
        user_id=user.id,
        role="user",
        content=message
        or f"Uploaded {len(statements)} statement file{'s' if len(statements) != 1 else ''}.",
        context={"import_ids": [str(statement.id) for statement in statements]},
    )
    db.add(user_message)
    if statements:
        reply, context = _import_reply(statements)
    else:
        reply, context = (
            _motion_reply(db, user.id, message)
            or _investigate_reply(db, user.id, message)
            or _report_reply(db, user.id, message)
            or _workspace_reply(db, user.id, message)
        )
    assistant_message = AssistantMessage(
        user_id=user.id,
        role="assistant",
        content=reply,
        context=context,
    )
    db.add(assistant_message)
    db.commit()
    db.refresh(assistant_message)
    return assistant_message
