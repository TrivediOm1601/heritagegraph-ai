"""C2 — user and session repository.

Every function is wrapped in :func:`app.db.session.safe`, so raw driver
errors never reach an API consumer. Lookups are by *normalised* email;
no query anywhere in this module accepts a user id derived from a request
body — callers pass the id taken from the validated session.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import User, UserSession
from ..db.session import ConflictError, safe

__all__ = [
    "UserAccount",
    "create_user",
    "get_user_by_email",
    "get_user_by_id",
    "create_session",
    "get_session",
    "delete_session",
    "delete_expired_sessions",
    "touch_session",
]

#: Cap on stored client labels (never used for authorization).
MAX_USER_AGENT = 255


@dataclass(frozen=True)
class UserAccount:
    """Public view of a user row — password hash deliberately excluded."""

    id: str
    email: str
    display_name: str | None

    def to_payload(self) -> dict[str, str | None]:
        return {"id": self.id, "email": self.email, "displayName": self.display_name}


def _public(user: User) -> UserAccount:
    return UserAccount(id=user.id, email=user.email, display_name=user.display_name)


@safe
async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password_hash: str,
    display_name: str | None = None,
    user_id: str | None = None,
) -> UserAccount:
    """Insert one user.

    Raises :class:`ConflictError` when the (normalised) address is already
    registered. Callers must translate that into a generic, non-enumerating
    API response.
    """
    existing = await session.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise ConflictError("An account with that email address already exists.")

    user = User(
        id=user_id or str(uuid.uuid4()),
        email=email,
        password_hash=password_hash,
        display_name=display_name,
    )
    session.add(user)
    await session.flush()
    return _public(user)


@safe
async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Fetch a user row by normalised email (includes the password hash)."""
    return await session.scalar(select(User).where(User.email == email))


@safe
async def get_user_by_id(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)


@safe
async def create_session(
    session: AsyncSession,
    *,
    user_id: str,
    token_hash: str,
    expires_at: datetime,
    user_agent: str | None = None,
) -> UserSession:
    """Create one server-side session row (keyed by the token digest)."""
    row = UserSession(
        token_hash=token_hash,
        user_id=user_id,
        expires_at=expires_at,
        user_agent=(user_agent or "")[:MAX_USER_AGENT] or None,
    )
    session.add(row)
    await session.flush()
    return row


@safe
async def get_session(session: AsyncSession, token_hash: str) -> tuple[UserSession, User] | None:
    """Fetch a live session and its user; expired rows are deleted.

    Returns ``None`` for unknown *and* expired tokens — a caller cannot
    distinguish the two, which is deliberate.
    """
    row = await session.get(UserSession, token_hash)
    if row is None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        await session.delete(row)
        await session.flush()
        return None
    user = await session.get(User, row.user_id)
    if user is None:
        # Orphaned session (user deleted): drop it, do not fail the request.
        await session.delete(row)
        await session.flush()
        return None
    return row, user


@safe
async def touch_session(session: AsyncSession, token_hash: str) -> None:
    """Record last use (best-effort telemetry; never extends the expiry)."""
    row = await session.get(UserSession, token_hash)
    if row is not None:
        row.last_seen_at = datetime.now(timezone.utc)
        await session.flush()


@safe
async def delete_session(session: AsyncSession, token_hash: str) -> bool:
    """Delete one session (logout). Returns whether a row was removed."""
    row = await session.get(UserSession, token_hash)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


@safe
async def delete_expired_sessions(session: AsyncSession) -> int:
    """Housekeeping: remove every expired session row."""
    result = await session.execute(
        delete(UserSession).where(UserSession.expires_at <= datetime.now(timezone.utc))
    )
    await session.flush()
    return int(result.rowcount or 0)
