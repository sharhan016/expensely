from datetime import UTC, datetime, timedelta

from fastapi import Cookie, Depends, HTTPException
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

password_hash = PasswordHash.recommended()
serializer = URLSafeTimedSerializer(settings.session_secret, salt="expense-analyzer-session")
SESSION_MAX_AGE = int(timedelta(days=30).total_seconds())


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_session(user_id: int) -> str:
    return serializer.dumps({"user_id": user_id, "issued_at": datetime.now(UTC).isoformat()})


def current_user(
    session: str | None = Cookie(default=None, alias="expense_session"),
    db: Session = Depends(get_db),
) -> User:
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = serializer.loads(session, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired) as exc:
        raise HTTPException(status_code=401, detail="Session expired") from exc
    user = db.get(User, payload["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="Account not found")
    return user
