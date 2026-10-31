"""Research workspace: collections, notes and merged collection reports.

Everything is owner-scoped in SQL. Adding an item validates that the target
archive/file belongs to the caller, so a collection can never reference (and
therefore never leak) another account's material.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..db.models import Archive, ArchiveFile, CollectionItem, ResearchCollection, ResearchNote
from .archive_report_service import render_archive_report
from .archive_service import archive_stats
from .heritage_service import DATASET_LABEL  # noqa: F401  (documented grouping)
from .pdf_service import PROJECT_NAME, _multiline, _register_fonts, _text
from ..db.models import TimelineEvent, Entity, ArchiveEntity

logger = get_logger("research")

__all__ = [
    "create_collection",
    "list_collections",
    "get_collection",
    "update_collection",
    "soft_delete_collection",
    "add_item",
    "remove_item",
    "list_items",
    "list_notes",
    "add_note",
    "update_note",
    "delete_note",
    "render_collection_report",
]

MAX_COLLECTION_ITEMS = 60


async def create_collection(
    session: AsyncSession, *, user_id: str, name: str, description: Optional[str]
) -> ResearchCollection:
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise ValueError("Collection name cannot be empty.")
    collection = ResearchCollection(
        id=str(uuid.uuid4()),
        user_id=user_id,
        name=cleaned[:200],
        description=(description or "").strip()[:4000] or None,
    )
    session.add(collection)
    await session.flush()
    return collection


async def list_collections(
    session: AsyncSession, *, user_id: str
) -> list[ResearchCollection]:
    statement = (
        select(ResearchCollection)
        .where(
            ResearchCollection.user_id == user_id,
            ResearchCollection.deleted_at.is_(None),
        )
        .order_by(ResearchCollection.updated_at.desc())
    )
    return list((await session.scalars(statement)).all())


async def get_collection(
    session: AsyncSession, collection_id: str, *, user_id: str
) -> Optional[ResearchCollection]:
    return await session.scalar(
        select(ResearchCollection).where(
            ResearchCollection.id == collection_id,
            ResearchCollection.user_id == user_id,
            ResearchCollection.deleted_at.is_(None),
        )
    )


async def update_collection(
    session: AsyncSession,
    collection: ResearchCollection,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> ResearchCollection:
    if name is not None:
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValueError("Collection name cannot be empty.")
        collection.name = cleaned[:200]
    if description is not None:
        collection.description = description.strip()[:4000] or None
    collection.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return collection


async def soft_delete_collection(
    session: AsyncSession, collection: ResearchCollection
) -> None:
    collection.deleted_at = datetime.now(timezone.utc)
    await session.flush()


async def add_item(
    session: AsyncSession,
    *,
    collection: ResearchCollection,
    target_type: str,
    target_id: str,
) -> None:
    """Attach an archive or file, validating ownership of the target."""
    if target_type not in ("archive", "file"):
        raise ValueError("Items must be an archive or a file.")
    count = (
        await session.scalar(
            select(func.count())
            .select_from(CollectionItem)
            .where(CollectionItem.collection_id == collection.id)
        )
        or 0
    )
    if count >= MAX_COLLECTION_ITEMS:
        raise ValueError(
            f"A collection can hold at most {MAX_COLLECTION_ITEMS} items."
        )

    if target_type == "archive":
        owned = await session.scalar(
            select(Archive.id).where(
                Archive.id == target_id,
                Archive.user_id == collection.user_id,
                Archive.deleted_at.is_(None),
            )
        )
        existing = await session.scalar(
            select(CollectionItem).where(
                CollectionItem.collection_id == collection.id,
                CollectionItem.archive_id == target_id,
            )
        )
    else:
        owned = await session.scalar(
            select(ArchiveFile.id).where(
                ArchiveFile.id == target_id,
                ArchiveFile.user_id == collection.user_id,
            )
        )
        existing = await session.scalar(
            select(CollectionItem).where(
                CollectionItem.collection_id == collection.id,
                CollectionItem.file_id == target_id,
            )
        )
    if owned is None:
        raise LookupError("That archive or file does not exist in your workspace.")
    if existing is not None:
        return  # idempotent: adding twice is not an error
    session.add(
        CollectionItem(
            collection_id=collection.id,
            archive_id=target_id if target_type == "archive" else None,
            file_id=target_id if target_type == "file" else None,
        )
    )
    collection.updated_at = datetime.now(timezone.utc)
    await session.flush()


async def remove_item(
    session: AsyncSession,
    *,
    collection: ResearchCollection,
    target_type: str,
    target_id: str,
) -> bool:
    statement = select(CollectionItem).where(CollectionItem.collection_id == collection.id)
    if target_type == "archive":
        statement = statement.where(CollectionItem.archive_id == target_id)
    elif target_type == "file":
        statement = statement.where(CollectionItem.file_id == target_id)
    else:
        raise ValueError("Items must be an archive or a file.")
    item = await session.scalar(statement)
    if item is None:
        return False
    await session.delete(item)
    collection.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return True


async def list_items(
    session: AsyncSession, *, collection: ResearchCollection
) -> list[dict]:
    rows = (
        await session.scalars(
            select(CollectionItem)
            .where(CollectionItem.collection_id == collection.id)
            .order_by(CollectionItem.added_at.asc())
        )
    ).all()
    archive_ids = [item.archive_id for item in rows if item.archive_id]
    file_ids = [item.file_id for item in rows if item.file_id]
    archives = {}
    if archive_ids:
        archives = {
            row.id: row
            for row in (
                await session.scalars(select(Archive).where(Archive.id.in_(archive_ids)))
            ).all()
        }
    files = {}
    if file_ids:
        files = {
            row.id: row
            for row in (
                await session.scalars(select(ArchiveFile).where(ArchiveFile.id.in_(file_ids)))
            ).all()
        }
    items: list[dict] = []
    for item in rows:
        if item.archive_id and item.archive_id in archives:
            items.append(
                {
                    "type": "archive",
                    "id": item.archive_id,
                    "name": archives[item.archive_id].name,
                    "addedAt": item.added_at,
                }
            )
        elif item.file_id and item.file_id in files:
            items.append(
                {
                    "type": "file",
                    "id": item.file_id,
                    "name": files[item.file_id].original_filename,
                    "addedAt": item.added_at,
                }
            )
    return items


async def list_notes(
    session: AsyncSession, *, collection_id: str, user_id: str
) -> list[ResearchNote]:
    statement = (
        select(ResearchNote)
        .where(
            ResearchNote.collection_id == collection_id,
            ResearchNote.user_id == user_id,
        )
        .order_by(ResearchNote.created_at.asc())
    )
    return list((await session.scalars(statement)).all())


async def add_note(
    session: AsyncSession,
    *,
    collection: ResearchCollection,
    body: str,
    subject_query: Optional[str] = None,
) -> ResearchNote:
    cleaned = (body or "").strip()
    if not cleaned:
        raise ValueError("A note cannot be empty.")
    note = ResearchNote(
        id=str(uuid.uuid4()),
        user_id=collection.user_id,
        collection_id=collection.id,
        subject_query=(subject_query or "").strip()[:512] or None,
        body=cleaned[:20000],
    )
    session.add(note)
    collection.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return note


async def update_note(
    session: AsyncSession, note: ResearchNote, *, body: str, user_id: str
) -> ResearchNote:
    if note.user_id != user_id:
        raise LookupError("No note exists with that id.")
    cleaned = (body or "").strip()
    if not cleaned:
        raise ValueError("A note cannot be empty.")
    note.body = cleaned[:20000]
    note.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return note


async def delete_note(session: AsyncSession, note: ResearchNote, *, user_id: str) -> None:
    if note.user_id != user_id:
        raise LookupError("No note exists with that id.")
    await session.delete(note)


# ---------------------------------------------------------------------------
# Collection report (cover + one archive report per included archive, merged)
# ---------------------------------------------------------------------------


def _cover_pdf(
    collection: ResearchCollection, notes: Sequence[ResearchNote], counts: dict
) -> bytes:
    regular, bold = _register_fonts()
    base = getSampleStyleSheet()
    title = ParagraphStyle(
        "CoverTitle",
        parent=base["Title"],
        fontName=bold,
        fontSize=24,
        leading=29,
        textColor=colors.HexColor("#134e4a"),
    )
    heading = ParagraphStyle(
        "CoverHeading",
        parent=base["Heading2"],
        fontName=bold,
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#0f766e"),
        spaceBefore=10,
        spaceAfter=4,
    )
    body = ParagraphStyle(
        "CoverBody",
        parent=base["BodyText"],
        fontName=regular,
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor("#1c1917"),
    )
    small = ParagraphStyle(
        "CoverSmall",
        parent=base["BodyText"],
        fontName=regular,
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#57534e"),
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=f"{collection.name} — HeritageGraph AI research collection",
        author=PROJECT_NAME,
    )
    story: list = [
        Paragraph("Research collection", title),
        Paragraph(_text(collection.name), heading),
    ]
    if collection.description:
        story.append(Paragraph(_multiline(collection.description), body))
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            f"{counts.get('archives', 0)} archive(s) · "
            f"{counts.get('files', 0)} file reference(s) · "
            f"{counts.get('notes', 0)} note(s)",
            small,
        )
    )
    if notes:
        story.append(Paragraph("Research notes", heading))
        for note in notes:
            story.append(
                Paragraph(
                    f"<b>{_text(note.subject_query or 'General')}</b><br/>{_multiline(note.body)}",
                    body,
                )
            )
            story.append(Spacer(1, 4))
    story.append(Paragraph("What follows", heading))
    story.append(
        Paragraph(
            "One section per archive in this collection follows: inventory, "
            "entity candidates, dated events, source references and limitations. "
            "Nothing in this document was generated as a historical claim; every "
            "passage is quoted or extracted from a file you uploaded.",
            body,
        )
    )
    story.append(Paragraph("Limitations", heading))
    story.append(
        Paragraph(
            "• Entity candidates are extraction output, not verified identification.<br/>"
            "• Dates are reproduced as written; approximate and century dates are not exact years.<br/>"
            "• Language is a script heuristic.<br/>"
            "• Duplicate detection flags possible duplicates for review; it never merges or deletes files.",
            small,
        )
    )

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(colors.HexColor("#78716c"))
        canvas.drawString(18 * mm, 10 * mm, PROJECT_NAME)
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


async def render_collection_report(
    session: AsyncSession,
    *,
    collection: ResearchCollection,
    user_email: str,
) -> bytes:
    """Merge the cover page with one report per included archive."""
    from pypdf import PdfReader, PdfWriter

    items = await list_items(session, collection=collection)
    archive_ids: list[str] = []
    for item in items:
        if item["type"] == "archive" and item["id"] not in archive_ids:
            archive_ids.append(item["id"])
        elif item["type"] == "file":
            file_row = await session.get(ArchiveFile, item["id"])
            if file_row and file_row.archive_id not in archive_ids:
                archive_ids.append(file_row.archive_id)

    notes = await list_notes(
        session, collection_id=collection.id, user_id=collection.user_id
    )
    file_count = sum(
        1
        for item in items
        if item["type"] == "file"
    )
    cover = _cover_pdf(
        collection,
        notes,
        {"archives": len(archive_ids), "files": file_count, "notes": len(notes)},
    )

    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(cover)))
    for archive_id in archive_ids[:12]:
        archive = await get_collection_archive(session, archive_id, collection.user_id)
        if archive is None:
            continue
        stats = await archive_stats(session, archive)
        files = list(
            (
                await session.scalars(
                    select(ArchiveFile)
                    .where(ArchiveFile.archive_id == archive.id)
                    .order_by(ArchiveFile.created_at.asc())
                )
            ).all()
        )
        entity_rows = (
            await session.execute(
                select(
                    Entity.name,
                    Entity.entity_type,
                    ArchiveEntity.extraction_method,
                    ArchiveEntity.page_number,
                )
                .join(ArchiveEntity, ArchiveEntity.entity_id == Entity.id)
                .join(ArchiveFile, ArchiveFile.id == ArchiveEntity.file_id)
                .where(ArchiveFile.archive_id == archive.id)
                .order_by(ArchiveEntity.confidence.desc())
                .limit(150)
            )
        ).all()
        timeline = list(
            (
                await session.scalars(
                    select(TimelineEvent)
                    .where(TimelineEvent.archive_id == archive.id)
                    .order_by(
                        TimelineEvent.year_start.is_(None),
                        TimelineEvent.year_start,
                        TimelineEvent.date_text,
                    )
                    .limit(150)
                )
            ).all()
        )
        payload = render_archive_report(
            archive,
            stats=stats,
            files=files,
            entities=[
                {
                    "name": name,
                    "type": type_,
                    "extraction_method": method,
                    "page_number": page,
                }
                for name, type_, method, page in entity_rows
            ],
            timeline=timeline,
            owner_email=user_email,
        )
        writer.append(PdfReader(io.BytesIO(payload)))

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


async def get_collection_archive(
    session: AsyncSession, archive_id: str, user_id: str
) -> Optional[Archive]:
    return await session.scalar(
        select(Archive).where(
            Archive.id == archive_id,
            Archive.user_id == user_id,
            Archive.deleted_at.is_(None),
        )
    )
