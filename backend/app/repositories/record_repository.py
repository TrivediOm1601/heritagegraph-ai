"""C3 — historical-record repository (Module C's retrieval data access).

Signature note (pending agreement with Members 2 and 4): ``session`` is
the first argument; the query is built entirely from bound parameters —
user input is escaped so it can never widen the LIKE pattern.

Also home of the development-seed loader
(``database/seeds/historical_records.json`` — see C3): parsing and
validation stay next to the table they populate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import HistoricalRecord
from ..db.session import ConflictError, safe

__all__ = [
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_LIMIT",
    "DEFAULT_SEED_PATH",
    "search_records",
    "load_seed_records",
    "seed_records",
]

#: Repository root (backend/app/repositories/record_repository.py -> parents[3]).
DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parents[3] / "database" / "seeds" / "historical_records.json"
)

DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100


def like_pattern(query: str) -> str:
    """Escape ``%``, ``_`` and ``\\`` so input cannot widen the pattern."""
    escaped = (
        query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


@safe
async def search_records(
    session: AsyncSession,
    query: str,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    offset: int = 0,
) -> list[HistoricalRecord]:
    """Case-insensitive substring search over title, content and source.

    * blank query -> ``[]`` (never an error, never an empty ``LIKE '%%'``
      full scan by accident),
    * ``limit`` is clamped to ``1..MAX_SEARCH_LIMIT`` and ``offset`` is
      never negative,
    * title matches rank above content/source matches,
    * portable: ``lower(col) LIKE lower(pattern) ESCAPE`` works on both
      PostgreSQL and SQLite (no PG-only ``ILIKE``).
    """
    text = (query or "").strip()
    if not text:
        return []

    bounded_limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
    bounded_offset = max(0, int(offset))
    pattern = like_pattern(text)

    hit_title = func.lower(HistoricalRecord.title).like(pattern, escape="\\")
    hit_content = func.lower(HistoricalRecord.content).like(pattern, escape="\\")
    hit_source = func.lower(HistoricalRecord.source).like(pattern, escape="\\")

    rank = case((hit_title, 0), else_=1)
    statement = (
        select(HistoricalRecord)
        .where(or_(hit_title, hit_content, hit_source))
        .order_by(rank, HistoricalRecord.title, HistoricalRecord.id)
        .limit(bounded_limit)
        .offset(bounded_offset)
    )
    return list((await session.scalars(statement)).all())


# ---------------------------------------------------------------------------
# C3 — development seed loading
# ---------------------------------------------------------------------------

_SEED_REQUIRED_FIELDS = ("id", "title", "year", "publisher", "url", "text", "provenance")


def load_seed_records(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Parse and validate the curated development-seed file.

    Returns one flat dict per record with the model's column names
    (``publisher`` -> ``source``, ``text`` -> ``content``) plus the
    curation fields (``url``, ``provenance``) kept for traceability.

    Raises :class:`ConflictError` with a human message on a malformed
    seed — never a raw parser traceback.
    """
    target = Path(path) if path is not None else DEFAULT_SEED_PATH
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConflictError(f"Historical-record seed file not found: {target.name}") from None
    except OSError as exc:
        raise ConflictError(f"Historical-record seed file could not be read: {target.name}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConflictError(
            f"Historical-record seed file is not valid JSON ({target.name}, "
            f"line {exc.lineno})."
        ) from exc

    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list) or not records:
        raise ConflictError("Historical-record seed file must contain a non-empty 'records' list.")

    prepared: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(records, start=1):
        if not isinstance(item, Mapping):
            raise ConflictError(f"Seed record #{index} must be an object.")
        missing = [
            field
            for field in _SEED_REQUIRED_FIELDS
            if not str(item.get(field) or "").strip()
        ]
        if missing:
            raise ConflictError(
                f"Seed record #{index} is missing required field(s): "
                + ", ".join(missing)
                + "."
            )
        record_id = str(item["id"]).strip()
        if record_id in seen_ids:
            raise ConflictError(f"Seed record #{index} repeats id {record_id!r}.")
        seen_ids.add(record_id)
        if not str(item["url"]).strip().lower().startswith(("http://", "https://")):
            raise ConflictError(
                f"Seed record {record_id!r} must cite an http(s) source URL."
            )
        prepared.append(
            {
                "id": record_id,
                "title": str(item["title"]).strip(),
                "year": str(item["year"]).strip(),
                "source": str(item["publisher"]).strip(),
                "url": str(item["url"]).strip(),
                "content": str(item["text"]).strip(),
                "provenance": str(item["provenance"]).strip(),
            }
        )
    return prepared


@safe
async def seed_records(
    session: AsyncSession,
    records: Sequence[Mapping[str, Any]],
) -> int:
    """Insert seed records that are not present yet; returns rows added.

    Idempotent: an existing ``id`` — or an existing ``(title, source)``
    pair under a different id — is skipped, so re-running the seed never
    duplicates rows and never violates C1's uniqueness rules. Add-only by
    design: curated rows are not overwritten by the seed file.

    One exception, because it is a repair rather than an overwrite: when a
    stored row has **no** source URL and the seed file supplies one, the
    URL is backfilled. Databases seeded before revision 0002 (which added
    the column) hold ``url = NULL`` for every row, so every "view source"
    link in the UI was dead until the row was recreated — the seed cannot
    otherwise reach existing rows.
    """
    added = 0
    for item in records:
        record_id = str(item["id"])
        existing = await session.get(HistoricalRecord, record_id)
        if existing is not None:
            if not (existing.url or "").strip() and item.get("url"):
                existing.url = item["url"]
            continue
        duplicate = (
            await session.scalars(
                select(HistoricalRecord).where(
                    HistoricalRecord.title == item["title"],
                    HistoricalRecord.source == item["source"],
                )
            )
        ).first()
        if duplicate is not None:
            if not (duplicate.url or "").strip() and item.get("url"):
                duplicate.url = item["url"]
            continue
        session.add(
            HistoricalRecord(
                id=record_id,
                title=item["title"],
                year=item.get("year"),
                source=item["source"],
                url=item.get("url"),  # validated http(s) by load_seed_records
                content=item.get("content"),
            )
        )
        added += 1
    await session.flush()
    return added
