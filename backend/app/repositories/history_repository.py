"""C2 — analysis-history repository.

Every query in this module takes ``user_id`` and filters on it. There is
deliberately **no** function that fetches an entry by id alone: a caller
must always pass the authenticated user's id, which is how ownership is
enforced on every history, detail and export path.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ANALYSIS_STATES, HISTORY_KINDS, HistoryEntry
from ..db.session import ConflictError, NotFoundError, safe

__all__ = [
    "UNSET",
    "create_entry",
    "get_entry",
    "list_entries",
    "count_entries",
    "update_entry",
    "update_by_analysis",
    "find_recent_search",
    "list_for_export",
    "summarize_search",
]


class _Unset:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"


UNSET = _Unset()

#: Longest preview kept on a history row.
SUMMARY_LIMIT = 360

#: Page-size bounds for the listing endpoint.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _validate_kind(kind: str) -> None:
    if kind not in HISTORY_KINDS:
        raise ConflictError(
            f"Unknown history kind {kind!r}; allowed: " + ", ".join(HISTORY_KINDS)
        )


def _validate_status(status: str) -> None:
    if status not in ANALYSIS_STATES:
        raise ConflictError(
            f"Unknown history status {status!r}; allowed: "
            + ", ".join(ANALYSIS_STATES)
        )


def summarize_search(query: str, results: Sequence[Any]) -> str:
    """One-line, source-backed preview of a text search."""
    if not results:
        return f"No records matched “{query}”."
    top = results[0]
    title = getattr(top, "title", None) or str(top)
    return (
        f"{len(results)} record{'s' if len(results) != 1 else ''} matched "
        f"“{query}” — best match: {title}."
    )[:SUMMARY_LIMIT]


@safe
async def create_entry(
    session: AsyncSession,
    *,
    user_id: str,
    kind: str,
    query: str | None = None,
    analysis_id: str | None = None,
    status: str = "queued",
    summary: str | None = None,
    snapshot: dict | None = None,
    entry_id: str | None = None,
) -> HistoryEntry:
    """Insert one history row owned by ``user_id``."""
    _validate_kind(kind)
    _validate_status(status)
    row = HistoryEntry(
        id=entry_id or str(uuid.uuid4()),
        user_id=user_id,
        kind=kind,
        query=(query or None),
        analysis_id=analysis_id,
        status=status,
        summary=(summary or None),
        snapshot=snapshot,
    )
    session.add(row)
    await session.flush()
    return row


@safe
async def get_entry(
    session: AsyncSession, entry_id: str, *, user_id: str
) -> HistoryEntry | None:
    """Fetch one entry **owned by** ``user_id`` (``None`` otherwise).

    A row belonging to somebody else is indistinguishable from a missing
    one, so an attacker cannot probe for other users' ids.
    """
    statement = select(HistoryEntry).where(
        HistoryEntry.id == entry_id, HistoryEntry.user_id == user_id
    )
    return await session.scalar(statement)


@safe
async def list_entries(
    session: AsyncSession,
    *,
    user_id: str,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    kind: str | None = None,
) -> list[HistoryEntry]:
    """This user's history, most recently updated first."""
    bounded_limit = max(1, min(int(limit), MAX_PAGE_SIZE))
    bounded_offset = max(0, int(offset))
    statement = select(HistoryEntry).where(HistoryEntry.user_id == user_id)
    if kind is not None:
        _validate_kind(kind)
        statement = statement.where(HistoryEntry.kind == kind)
    statement = (
        statement.order_by(HistoryEntry.updated_at.desc(), HistoryEntry.id.desc())
        .limit(bounded_limit)
        .offset(bounded_offset)
    )
    return list((await session.scalars(statement)).all())


@safe
async def count_entries(
    session: AsyncSession, *, user_id: str, kind: str | None = None
) -> int:
    statement = select(func.count()).select_from(HistoryEntry).where(
        HistoryEntry.user_id == user_id
    )
    if kind is not None:
        _validate_kind(kind)
        statement = statement.where(HistoryEntry.kind == kind)
    return int((await session.scalar(statement)) or 0)


@safe
async def update_entry(
    session: AsyncSession,
    entry_id: str,
    *,
    user_id: str,
    status: str | None | _Unset = UNSET,
    summary: str | None | _Unset = UNSET,
    snapshot: dict | None | _Unset = UNSET,
) -> HistoryEntry:
    """Owner-scoped partial update; ``None`` clears, UNSET leaves alone."""
    row = await get_entry(session, entry_id, user_id=user_id)
    if row is None:
        raise NotFoundError(f"History entry {entry_id!r} does not exist.")
    if not isinstance(status, _Unset):
        if status is None:
            raise ConflictError("History status cannot be empty.")
        _validate_status(status)
        row.status = status
    if not isinstance(summary, _Unset):
        row.summary = summary
    if not isinstance(snapshot, _Unset):
        row.snapshot = snapshot
    # Touch so the entry moves to the top of a most-recent-first list.
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return row


@safe
async def update_by_analysis(
    session: AsyncSession,
    analysis_id: str,
    *,
    status: str,
    summary: str | None | _Unset = UNSET,
) -> int:
    """Keep the history row of an image analysis in step with its run.

    Scoped by ``analysis_id`` only: the run id is a server-generated UUID
    that is itself ownership-checked before any analysis endpoint answers,
    and this function is called from the processing pipeline (server side),
    never from a request body. Returns the number of updated rows.
    """
    _validate_status(status)
    rows = list(
        (
            await session.scalars(
                select(HistoryEntry).where(
                    HistoryEntry.analysis_id == analysis_id,
                    HistoryEntry.kind == "image_analysis",
                )
            )
        ).all()
    )
    for row in rows:
        row.status = status
        if not isinstance(summary, _Unset):
            row.summary = summary
        row.updated_at = datetime.now(timezone.utc)
    if rows:
        await session.flush()
    return len(rows)


@safe
async def find_recent_search(
    session: AsyncSession,
    *,
    user_id: str,
    query: str,
    within_minutes: int = 30,
) -> HistoryEntry | None:
    """The most recent identical text search inside the dedupe window.

    Frontend retries, double submits and re-focusing a subject must not
    fill the history with duplicate rows, so a repeat within the window
    updates that row instead of inserting a new one.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, within_minutes))
    statement = (
        select(HistoryEntry)
        .where(
            HistoryEntry.user_id == user_id,
            HistoryEntry.kind == "text_search",
            HistoryEntry.query == query,
            HistoryEntry.updated_at >= cutoff,
        )
        .order_by(HistoryEntry.updated_at.desc())
        .limit(1)
    )
    return await session.scalar(statement)


@safe
async def list_for_export(
    session: AsyncSession,
    *,
    user_id: str,
    entry_ids: Sequence[str] | None = None,
    limit: int = 500,
) -> list[HistoryEntry]:
    """This user's entries for a PDF export (never another user's).

    ``entry_ids`` is validated as a *filter*: rows not owned by ``user_id``
    simply do not match, so a guessed id cannot widen the export.
    """
    statement = select(HistoryEntry).where(HistoryEntry.user_id == user_id)
    if entry_ids:
        statement = statement.where(HistoryEntry.id.in_(list(entry_ids)))
    statement = (
        statement.order_by(HistoryEntry.updated_at.desc(), HistoryEntry.id.desc())
        .limit(max(1, min(int(limit), 1000)))
    )
    return list((await session.scalars(statement)).all())
