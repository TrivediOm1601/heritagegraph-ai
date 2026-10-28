"""C2 — entity repository (canonical extracted-entity storage).

Signature note (pending agreement with Members 2 and 4): repository
functions take the ``AsyncSession`` as their first argument, so the B3
orchestrator owns one transactional scope (:func:`app.db.session.
session_scope`) for a whole workflow and commits exactly once.
"""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ENTITY_TYPES, Entity
from ..db.session import ConflictError, safe

__all__ = [
    "EntityInput",
    "normalize_name",
    "find_entities_by_normalized_names",
    "upsert_entities",
]


def normalize_name(name: str) -> str:
    """Canonical matching key: collapsed whitespace + casefold.

    Python-side (not SQL-side) so the rule is identical on PostgreSQL
    and SQLite; the SQL side pre-filters with ``lower(trim(name))`` and
    the final comparison happens here.
    """
    return " ".join(name.split()).casefold()


@safe
async def find_entities_by_normalized_names(
    session: AsyncSession,
    names: Sequence[str],
    *,
    entity_type: str | None = None,
) -> list[Entity]:
    """Fetch canonical entities matching any of ``names`` (normalised).

    Matching is case- and whitespace-insensitive; ``entity_type`` narrows
    the lookup (e.g. only ``person``). Returns ``[]`` for empty input —
    an empty ``IN``/``OR`` must never be emitted.
    """
    normalised = [normalize_name(name) for name in names if name and name.strip()]
    if not normalised:
        return []

    lowered = {name.casefold() for name in normalised}
    wanted = set(normalised)

    statement = (
        select(Entity)
        .where(func.lower(func.trim(Entity.name)).in_(lowered))
        .order_by(Entity.name, Entity.entity_type)
    )
    if entity_type is not None:
        statement = statement.where(Entity.entity_type == entity_type)

    candidates = (await session.scalars(statement)).all()
    # Final pass in Python: normalisation must match exactly, not merely
    # after SQL's ASCII-only lower()/trim().
    return [entity for entity in candidates if normalize_name(entity.name) in wanted]


class EntityInput:
    """Creation payload for :func:`upsert_entities` (accepts kwargs)."""

    __slots__ = ("name", "entity_type", "confidence")

    def __init__(self, name: str, entity_type: str, confidence: float | None = None):
        self.name = name
        self.entity_type = entity_type
        self.confidence = confidence


@safe
async def upsert_entities(
    session: AsyncSession,
    items: Sequence[EntityInput | dict],
) -> list[Entity]:
    """Find-or-create canonical entities by ``(name, entity_type)``.

    Duplicates inside ``items`` and rows that already exist collapse onto
    one canonical entity (C1's uniqueness rule), so Module D's extraction
    output can be persisted idempotently.
    """
    prepared: list[tuple[str, str]] = []
    for item in items:
        data = item if isinstance(item, dict) else {
            "name": item.name,
            "entity_type": item.entity_type,
        }
        name = str(data.get("name", "")).strip()
        entity_type = str(data.get("entity_type", "")).strip()
        if not name or not entity_type:
            raise ConflictError("An entity needs both a name and a type.")
        if entity_type not in ENTITY_TYPES:
            raise ConflictError(
                f"Unknown entity type {entity_type!r}; allowed types are: "
                + ", ".join(ENTITY_TYPES)
            )
        prepared.append((name, entity_type))

    if not prepared:
        return []

    result: list[Entity] = []
    seen: set[tuple[str, str]] = set()
    for name, entity_type in prepared:
        # Deduplicate on the SAME normalisation rule as
        # find_entities_by_normalized_names, so "Ada Lovelace" and
        # "  ada   LOVELACE " never become two canonical entities.
        key = (normalize_name(name), entity_type)
        if key in seen:
            continue
        seen.add(key)

        candidates = (
            await session.scalars(
                select(Entity).where(
                    Entity.entity_type == entity_type,
                    func.lower(func.trim(Entity.name)).in_({key[0]}),
                )
            )
        ).all()
        existing = next(
            (entity for entity in candidates if normalize_name(entity.name) == key[0]),
            None,
        )
        if existing is not None:
            result.append(existing)
            continue

        entity = Entity(name=name, entity_type=entity_type)
        try:
            # SAVEPOINT around the insert: two analyses can extract the
            # same entity at the same time (concurrent uploads), so the
            # SELECT above may miss a row that commits before our INSERT
            # lands — ``uq_entities_name_type`` then rejects it. Without
            # the savepoint that IntegrityError would poison the whole
            # transaction and fail an otherwise valid analysis.
            async with session.begin_nested():
                session.add(entity)
        except IntegrityError:
            # The concurrent writer won: adopt its canonical row so both
            # analyses reference the same entity. If it is somehow not
            # visible, re-raise — an honest failure beats a lost entity.
            winners = (
                await session.scalars(
                    select(Entity).where(
                        Entity.entity_type == entity_type,
                        func.lower(func.trim(Entity.name)).in_({key[0]}),
                    )
                )
            ).all()
            adopted = next(
                (
                    candidate
                    for candidate in winners
                    if normalize_name(candidate.name) == key[0]
                ),
                None,
            )
            if adopted is None:
                raise
            result.append(adopted)
            continue
        result.append(entity)

    return result
