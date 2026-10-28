"""Assemble PDF export data from stored, owner-scoped history rows.

The route authenticates and loads the user's history rows; this module
turns each row into a :class:`~app.services.pdf_service.PdfEntry`. It only
ever reads rows already filtered by ``user_id`` — it has no way to fetch a
different account's data.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..db.models import HistoryEntry
from ..repositories.analysis_repository import get_analysis_run, list_analysis_entities
from ..repositories.evidence_repository import list_evidence
from ..repositories.relationship_repository import list_relationships
from ..services.pdf_service import PdfDocument, PdfEdge, PdfEntity, PdfEntry, PdfSource

logger = get_logger("export")

__all__ = ["entry_from_history", "document_from_entries", "MAX_EVIDENCE_PER_EDGE"]

#: Evidence items included per relationship (a PDF is a summary, not a dump).
MAX_EVIDENCE_PER_EDGE = 3

#: Maximum sources listed per record.
MAX_SOURCES = 12


def _search_entry(row: HistoryEntry) -> PdfEntry:
    snapshot = row.snapshot if isinstance(row.snapshot, dict) else {}
    briefing = snapshot.get("briefing") if isinstance(snapshot.get("briefing"), dict) else {}
    results = snapshot.get("results") if isinstance(snapshot.get("results"), list) else []

    sources: list[PdfSource] = []
    for source in (briefing or {}).get("sources", []) or []:
        if isinstance(source, dict) and source.get("title"):
            sources.append(
                PdfSource(
                    title=str(source.get("title")),
                    url=source.get("url"),
                    publisher=source.get("publisher"),
                    excerpt=source.get("excerpt"),
                )
            )
    if not sources:
        for result in results[:MAX_SOURCES]:
            if isinstance(result, dict) and result.get("title"):
                sources.append(
                    PdfSource(
                        title=str(result.get("title")),
                        url=result.get("url"),
                        publisher=result.get("source"),
                        excerpt=result.get("excerpt"),
                    )
                )

    return PdfEntry(
        title=row.query or "Historical text search",
        kind="text_search",
        status=row.status,
        created_at=row.updated_at or row.created_at,
        summary=row.summary,
        search_query=row.query,
        explanation=(briefing or {}).get("explanation")
        or (briefing or {}).get("summary"),
        sources=sources[:MAX_SOURCES],
        warnings=tuple(
            str(item) for item in (briefing or {}).get("uncertainty", []) or []
        )[:6],
    )


async def _image_entry(session: AsyncSession, row: HistoryEntry) -> PdfEntry:
    limitations: list[str] = []
    run = await get_analysis_run(session, row.analysis_id) if row.analysis_id else None
    if run is None:
        return PdfEntry(
            title=row.query or "Image analysis",
            kind="image_analysis",
            status=row.status,
            created_at=row.updated_at or row.created_at,
            summary=row.summary,
            limitations=(
                "The stored analysis is no longer available; only this history "
                "entry could be exported.",
            ),
        )

    briefing = run.briefing if isinstance(run.briefing, dict) else {}
    links = await list_analysis_entities(session, run.id)
    relationships = await list_relationships(session, run.id)

    entities = tuple(
        PdfEntity(
            name=entity.name,
            type=entity.entity_type,
            confidence=link.confidence,
        )
        for entity, link in links
    )
    labels = {entity.id: entity.name for entity, _link in links}

    edges: list[PdfEdge] = []
    for relationship in relationships:
        evidence_rows = await list_evidence(session, relationship.id)
        evidence = tuple(
            PdfSource(
                title=item.source,
                url=item.url,
                excerpt=item.excerpt,
            )
            for item in evidence_rows[:MAX_EVIDENCE_PER_EDGE]
        )
        edges.append(
            PdfEdge(
                source=labels.get(relationship.source_entity_id, relationship.source_entity_id),
                target=labels.get(relationship.target_entity_id, relationship.target_entity_id),
                label=relationship.label,
                verification_status=relationship.verification_status,
                evidence=evidence,
            )
        )

    sources: list[PdfSource] = []
    for source in briefing.get("sources", []) or []:
        if isinstance(source, dict) and source.get("title"):
            sources.append(
                PdfSource(
                    title=str(source.get("title")),
                    url=source.get("url"),
                    publisher=source.get("publisher"),
                    excerpt=source.get("excerpt"),
                )
            )

    limitations.append(
        "The uploaded image is not retained; this export contains the "
        "extracted text and metadata only."
    )
    if not sources:
        limitations.append("No cited source was recorded for this analysis.")
    if not relationships:
        limitations.append("No supported relationships were recorded.")

    return PdfEntry(
        title=run.original_filename or row.query or "Image analysis",
        kind="image_analysis",
        status=run.status,
        created_at=run.created_at,
        summary=row.summary,
        extracted_text=run.extracted_text,
        interpretation=run.interpretation,
        explanation=briefing.get("explanation") or briefing.get("summary"),
        entities=entities,
        edges=tuple(edges),
        sources=tuple(sources[:MAX_SOURCES]),
        warnings=tuple(str(item) for item in (run.warnings or [])[:8]),
        error=run.error,
        limitations=tuple(limitations),
    )


async def entry_from_history(
    session: AsyncSession, row: HistoryEntry
) -> Optional[PdfEntry]:
    """One history row → one PDF section (``None`` only on unexpected failure)."""
    try:
        if row.kind == "text_search":
            return _search_entry(row)
        return await _image_entry(session, row)
    except Exception as exc:  # noqa: BLE001 — one bad row must not break the export
        logger.warning(
            "history entry %s could not be rendered for export: %s",
            row.id,
            type(exc).__name__,
        )
        return PdfEntry(
            title=row.query or "Record",
            kind=row.kind,
            status=row.status,
            created_at=row.updated_at or row.created_at,
            summary=row.summary,
            limitations=(
                "This record could not be fully read from storage; its stored "
                "summary is shown instead.",
            ),
        )


async def document_from_entries(
    session: AsyncSession,
    *,
    rows: Sequence[HistoryEntry],
    user_email: str,
) -> PdfDocument:
    """Build a complete export document from already owner-scoped rows."""
    entries: list[PdfEntry] = []
    for row in rows:
        entry = await entry_from_history(session, row)
        if entry is not None:
            entries.append(entry)
    return PdfDocument(entries=tuple(entries), user_email=user_email)
