"""History orchestration: record, read and reopen a user's activity.

Both activity types (text search and image analysis) are written here, so
the rules live in one place:

* writes always carry the authenticated ``user_id``;
* a repeated identical text search inside the dedupe window updates the
  existing row instead of inserting a duplicate;
* a stored result is *replayed*, never re-generated — reopening a history
  entry must not spend provider credits or change the answer.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..db.models import AnalysisRun, HistoryEntry
from ..repositories.analysis_repository import get_analysis_run, list_analysis_entities
from ..repositories.history_repository import (
    create_entry,
    find_recent_search,
    summarize_search,
    update_entry,
)
from ..repositories.relationship_repository import list_relationships
from ..schemas.analysis import AnalysisResponse
from ..schemas.graph import GraphEdge, GraphNode, GraphResponse
from ..schemas.history import (
    HistoryDetailResponse,
    HistoryItem,
    SearchSnapshot,
)
from ..schemas.search import HistoricalMatch

logger = get_logger("history")

__all__ = [
    "DEDUPE_WINDOW_MINUTES",
    "record_text_search",
    "to_item",
    "build_detail",
    "IMAGE_NOT_RETAINED",
]

#: A repeated identical search inside this window is the same activity.
DEDUPE_WINDOW_MINUTES = 30

#: Stored search snapshots keep at most this many results.
SNAPSHOT_RESULT_LIMIT = 8

#: Honest limitation shown when reopening an image analysis.
IMAGE_NOT_RETAINED = (
    "The uploaded image itself is not stored: only its extracted text, "
    "entities, relationships and sources are kept."
)


def to_item(row: HistoryEntry) -> HistoryItem:
    return HistoryItem(
        id=row.id,
        kind=row.kind,  # type: ignore[arg-type]
        query=row.query,
        analysis_id=row.analysis_id,
        status=row.status,
        summary=row.summary,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def record_text_search(
    session: AsyncSession,
    *,
    user_id: str,
    query: str,
    results: Sequence[HistoricalMatch],
    briefing: Optional[dict] = None,
    notice: Optional[str] = None,
) -> HistoryEntry:
    """Persist one text search for ``user_id`` (deduplicated).

    ``results``/``briefing`` are the contract payloads already returned to
    the user; only the first :data:`SNAPSHOT_RESULT_LIMIT` results are kept
    so a history row stays small. No provider is called here.
    """
    snapshot = {
        "query": query,
        "results": [
            result.model_dump(mode="json", by_alias=True)
            for result in list(results)[:SNAPSHOT_RESULT_LIMIT]
        ],
        "briefing": briefing,
        "notice": notice,
    }
    summary = summarize_search(query, list(results))

    existing = await find_recent_search(
        session,
        user_id=user_id,
        query=query,
        within_minutes=DEDUPE_WINDOW_MINUTES,
    )
    if existing is not None:
        # Same user, same query, recent: update instead of duplicating.
        return await update_entry(
            session,
            existing.id,
            user_id=user_id,
            status="completed",
            summary=summary,
            snapshot=snapshot,
        )

    return await create_entry(
        session,
        user_id=user_id,
        kind="text_search",
        query=query,
        status="completed",
        summary=summary,
        snapshot=snapshot,
    )


def _graph_of(
    analysis_id: str, links: Sequence, relationships: Sequence
) -> GraphResponse:
    """Rebuild the stored graph exactly as the analysis endpoint serves it."""
    return GraphResponse(
        analysis_id=analysis_id,
        nodes=[
            GraphNode(id=entity.id, label=entity.name, type=entity.entity_type)
            for entity, _link in links
        ],
        edges=[
            GraphEdge(
                id=relationship.id,
                source=relationship.source_entity_id,
                target=relationship.target_entity_id,
                label=relationship.label,
                verification_status=relationship.verification_status,
            )
            for relationship in relationships
        ],
    )


async def build_detail(
    session: AsyncSession,
    *,
    entry: HistoryEntry,
    user_id: str,
    analysis_response: Optional[AnalysisResponse] = None,
) -> HistoryDetailResponse:
    """Assemble the reopenable view of one history entry.

    ``user_id`` is passed explicitly and re-checked against the run row:
    even if a stale row pointed at another account's analysis, the result
    would not be returned.
    """
    limitations: list[str] = []
    analysis: Optional[AnalysisResponse] = None
    graph: Optional[GraphResponse] = None
    search: Optional[SearchSnapshot] = None

    if entry.kind == "text_search":
        snapshot = entry.snapshot if isinstance(entry.snapshot, dict) else {}
        try:
            search = SearchSnapshot(**snapshot)
        except Exception:  # noqa: BLE001 — incompatible stored shape
            limitations.append(
                "The stored search result could not be read; showing its summary only."
            )
    elif entry.analysis_id:
        run: Optional[AnalysisRun] = await get_analysis_run(session, entry.analysis_id)
        if run is None:
            limitations.append(
                "The analysis this history entry points to is no longer available."
            )
        elif run.user_id != user_id:
            # Defence in depth: never serve another account's analysis.
            limitations.append(
                "This analysis is no longer available to your account."
            )
        else:
            analysis = analysis_response
            links = await list_analysis_entities(session, run.id)
            relationships = await list_relationships(session, run.id)
            graph = _graph_of(run.id, links, relationships)
            limitations.append(IMAGE_NOT_RETAINED)
            if run.status == "failed":
                limitations.append("This analysis failed; no results were produced.")
            elif run.status == "partial":
                limitations.append(
                    "This analysis completed partially: some processing stages "
                    "were degraded (see its warnings)."
                )
            elif run.status in ("queued", "processing"):
                limitations.append("This analysis had not finished when it was recorded.")
    else:
        limitations.append("This history entry has no stored result.")

    return HistoryDetailResponse(
        entry=to_item(entry),
        analysis=analysis,
        graph=graph,
        search=search,
        limitations=limitations,
    )
