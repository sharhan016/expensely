import re
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.investigate import tools
from app.investigate.planner import investigate
from app.models import Account, StatementImport, Transaction, User
from app.security import current_user

router = APIRouter(prefix="/investigate", tags=["where did it go"])
_REF = re.compile(
    r"^(txn:\d{1,10}|stmt:[0-9a-f-]{36}|cov:\d{1,10}:\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}"
    r"|disc:\d{1,10}:[0-9a-f-]{36}:[0-9a-f-]{36}|scope:(all|\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}))$"
)


class AskBody(BaseModel):
    message: str = Field(max_length=500)


class EvidenceBody(BaseModel):
    refs: list[str] = Field(max_length=50)


@router.post("")
def ask(body: AskBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return investigate(db, user.id, body.message)


@router.post("/tools/{name}")
def run_tool(
    name: str,
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if name not in tools.TOOLS:
        raise HTTPException(status_code=404, detail="Unknown tool")
    model, function = tools.TOOLS[name]
    try:
        data = model.model_validate(payload)
    except ValidationError as exc:
        # Field names only: the rejected values are never echoed or logged.
        fields = sorted(
            {str(error["loc"][0]) if error["loc"] else "input" for error in exc.errors()}
        )
        raise HTTPException(status_code=422, detail=f"Invalid input: {', '.join(fields)}") from None
    try:
        return function(db, user.id, data)
    except tools.ToolError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc


@router.post("/evidence")
def evidence(body: EvidenceBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Open the records behind an answer. Anything not owned (or not real) is a 404."""
    if any(not _REF.match(ref) for ref in body.refs):
        raise HTTPException(status_code=422, detail="Invalid evidence reference")
    txn_ids, statement_ids, notes = [], [], []
    for ref in body.refs:
        kind, _, rest = ref.partition(":")
        if kind == "txn":
            txn_ids.append(int(rest))
        elif kind == "stmt":
            statement_ids.append(uuid.UUID(rest))
        elif kind in {"cov", "disc"}:
            account_id = int(rest.split(":")[0])
            if not db.scalar(
                select(Account.id).where(Account.id == account_id, Account.user_id == user.id)
            ):
                raise HTTPException(status_code=404, detail="Not found")
            if kind == "disc":
                statement_ids += [uuid.UUID(part) for part in rest.split(":")[1:]]
            notes.append(ref)
        else:
            notes.append(ref)
    owned = {
        item.id: item
        for item in db.scalars(
            select(Transaction).where(Transaction.user_id == user.id, Transaction.id.in_(txn_ids))
        )
    }
    statements = {
        item.id: item
        for item in db.scalars(
            select(StatementImport).where(
                StatementImport.user_id == user.id, StatementImport.id.in_(set(statement_ids))
            )
        )
    }
    if len(owned) != len(set(txn_ids)) or len(statements) != len(set(statement_ids)):
        raise HTTPException(status_code=404, detail="Not found")
    names = dict(
        db.execute(select(Account.id, Account.name).where(Account.user_id == user.id)).all()
    )
    return {
        "transactions": [
            {
                "id": owned[i].id,
                "txn_date": owned[i].txn_date.isoformat(),
                "account_name": names.get(owned[i].account_id, ""),
                "description": owned[i].description,
                "amount": f"{owned[i].amount:.2f}",
            }
            for i in dict.fromkeys(txn_ids)
        ],
        "statements": [
            {
                "id": str(item.id),
                "period_start": item.period_start.isoformat() if item.period_start else None,
                "period_end": item.period_end.isoformat() if item.period_end else None,
            }
            for item in statements.values()
        ],
        "notes": notes,
    }


_ = date
