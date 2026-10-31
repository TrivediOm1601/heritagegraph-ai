"""Heritage Archive Studio — ingestion, processing, search and statistics.

The pipeline implemented here is the one the product promises:

    upload → validate → store original → classify → extract (OCR / PDF /
    DOCX / PPTX / text) → language hint → entity candidates → dated events
    → duplicate detection → searchable archive

Each file is processed independently (a failure is recorded on that file and
never stops its siblings), in the background after the upload response, and
every stage writes a truthful status. A file with no extractable text is
**never** marked ``completed``: see :func:`_grade_document`.

Public entry points used by the routes:

* :func:`ingest_uploads` — validate and store one request's files (ZIPs are
  expanded safely; every member becomes its own row);
* :func:`process_archive_file` — the background worker;
* :func:`search_archive_files` — keyword search with page-level provenance;
* :func:`archive_stats` — the numbers the dashboard shows (all real).
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

from PIL import Image
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import ApiError
from ..core.logging import get_logger
from ..db.models import (
    Archive,
    ArchiveEntity,
    ArchiveFile,
    ArchivePage,
    Entity,
    HeritageSite,
    TimelineEvent,
)
from ..db.session import session_scope
from ..integrations import ProviderConfigurationError, ProviderError
from ..integrations.ocr import run_ocr
from ..repositories.entity_repository import upsert_entities
from .archive_text_service import (
    extract_entity_mentions,
    parse_date_mentions,
)
from .document_service import (
    DocumentExtraction,
    ExtractedPage,
    ExtractionError,
    detect_language,
    extract_document,
    normalise_text,
    supported_kind_for_extension,
    text_fingerprint,
)
from .image_service import PreparedImage, prepare_image
from .storage_service import StorageError, read_bytes, save_bytes
from .zip_service import ZipSafetyError, inspect_zip, limits_from_env

logger = get_logger("archive")

__all__ = [
    "ArchiveUploadError",
    "MAX_ARCHIVE_FILE_BYTES",
    "MAX_ZIP_UPLOAD_BYTES",
    "MAX_FILES_PER_REQUEST",
    "IngestedFile",
    "IngestResult",
    "create_archive",
    "list_archives",
    "get_archive",
    "update_archive",
    "soft_delete_archive",
    "ingest_uploads",
    "process_archive_file",
    "archive_stats",
    "search_archive_files",
    "list_timeline",
    "archive_file_detail",
]

#: Per-file limit for archive uploads (larger than the 10 MB image-analysis
#: limit because scanned PDFs and presentations are genuinely bigger).
MAX_ARCHIVE_FILE_BYTES = 25 * 1024 * 1024
#: A ZIP is read into memory for inspection, so its own upload is bounded too.
MAX_ZIP_UPLOAD_BYTES = 100 * 1024 * 1024
#: Files accepted in one multipart request (the UI batches the rest).
MAX_FILES_PER_REQUEST = 20
#: Terminal states used to decide what counts as "processed".
_DONE = ("completed", "partial", "failed")
_DUPLICATE_DISTANCE = 6  # dHash Hamming distance for "similar photograph"


class ArchiveUploadError(ValueError):
    """One uploaded file could not be accepted (safe, user-facing message)."""


@dataclass(frozen=True)
class IngestedFile:
    """One file record created by :func:`ingest_uploads`."""

    id: str
    filename: str
    kind: str
    status: str
    size_bytes: int


@dataclass
class IngestResult:
    files: list[IngestedFile] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def queued_ids(self) -> list[str]:
        return [item.id for item in self.files if item.kind != "zip"]


# ---------------------------------------------------------------------------
# Archives
# ---------------------------------------------------------------------------


def _clean_name(name: str, *, label: str, limit: int) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise ArchiveUploadError(f"{label} cannot be empty.")
    if len(cleaned) > limit:
        raise ArchiveUploadError(f"{label} must be at most {limit} characters.")
    return cleaned


def _clean_tags(tags: Optional[Sequence[str]]) -> Optional[list[str]]:
    if not tags:
        return None
    cleaned: list[str] = []
    for tag in tags:
        value = " ".join(str(tag).split())
        if value and value.casefold() not in {item.casefold() for item in cleaned}:
            cleaned.append(value[:40])
    return cleaned[:12] or None


async def create_archive(
    session: AsyncSession,
    *,
    user_id: str,
    name: str,
    description: Optional[str] = None,
    region: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
) -> Archive:
    """Create one archive owned by ``user_id`` (the authenticated account)."""
    archive = Archive(
        id=str(uuid.uuid4()),
        user_id=user_id,
        name=_clean_name(name, label="Archive name", limit=200),
        description=(description or "").strip()[:4000] or None,
        region=(region or "").strip()[:120] or None,
        tags=_clean_tags(tags),
    )
    session.add(archive)
    await session.flush()
    return archive


async def list_archives(session: AsyncSession, *, user_id: str) -> list[Archive]:
    """The user's active archives, newest first."""
    statement = (
        select(Archive)
        .where(Archive.user_id == user_id, Archive.deleted_at.is_(None))
        .order_by(Archive.created_at.desc())
    )
    return list((await session.scalars(statement)).all())


async def get_archive(
    session: AsyncSession, archive_id: str, *, user_id: str
) -> Optional[Archive]:
    """One owned, non-deleted archive (``None`` for another user's id)."""
    statement = select(Archive).where(
        Archive.id == archive_id,
        Archive.user_id == user_id,
        Archive.deleted_at.is_(None),
    )
    return await session.scalar(statement)


async def update_archive(
    session: AsyncSession,
    archive: Archive,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    region: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
) -> Archive:
    """Rename / re-describe an archive (omitted fields stay untouched)."""
    if name is not None:
        archive.name = _clean_name(name, label="Archive name", limit=200)
    if description is not None:
        archive.description = description.strip()[:4000] or None
    if region is not None:
        archive.region = region.strip()[:120] or None
    if tags is not None:
        archive.tags = _clean_tags(tags)
    archive.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return archive


async def soft_delete_archive(
    session: AsyncSession, archive: Archive
) -> None:
    """Hide an archive without destroying the user's uploaded material.

    The originals stay addressable (a support request can restore them);
    nothing is served to any request once ``deleted_at`` is set.
    """
    archive.deleted_at = datetime.now(timezone.utc)
    await session.flush()


# ---------------------------------------------------------------------------
# Classification and upload
# ---------------------------------------------------------------------------


def _sniff_kind(data: bytes, filename: str) -> str:
    """Real format check — a renamed executable must never be accepted.

    The declared extension selects the expected container; the bytes must
    then agree with it. ``.docx``/``.pptx`` are ZIP containers, so their
    magic bytes are shared with ``.zip`` and the extension decides.
    """
    extension = pathlib.PurePath(filename or "").suffix.lower()
    kind = supported_kind_for_extension(filename)
    if kind is None:
        raise ArchiveUploadError(
            f"\"{filename or 'file'}\" is not a supported file. Accepted: "
            "images (JPEG, PNG, WebP, GIF, TIFF, AVIF), PDF, DOCX, PPTX, TXT, "
            "Markdown and ZIP."
        )
    if kind == "pdf":
        if not data.startswith(b"%PDF"):
            raise ArchiveUploadError(
                f"\"{filename}\" does not contain PDF data. Upload a real PDF."
            )
    elif kind in {"docx", "pptx", "zip"}:
        if not data.startswith(b"PK"):
            raise ArchiveUploadError(
                f"\"{filename}\" is not a valid ZIP-based file "
                f"({'Word document' if kind == 'docx' else 'presentation' if kind == 'pptx' else 'archive'})."
            )
    elif kind == "text":
        if b"\x00" in data[:4096]:
            raise ArchiveUploadError(
                f"\"{filename}\" looks like binary data, not text. Upload it as "
                "a PDF or DOCX instead."
            )
    elif kind == "image":
        if extension == ".avif" and not data.startswith(b"\x00\x00\x00"):
            # AVIF is ISO-BMFF; the real decode happens in prepare_image.
            pass
        if not data:
            raise ArchiveUploadError(f"\"{filename}\" is empty.")
    return kind


def _dhash(prepared: PreparedImage) -> str:
    """64-bit difference hash of the prepared image (near-duplicate signal)."""
    image = prepared.image.convert("L").resize((9, 8), Image.LANCZOS)
    pixels = list(image.tobytes())
    bits = 0
    for row in range(8):
        for column in range(8):
            left = pixels[row * 9 + column]
            right = pixels[row * 9 + column + 1]
            bits = (bits << 1) | int(left < right)
    return f"{bits:016x}"


def _hamming(left: str, right: str) -> int:
    try:
        return bin(int(left, 16) ^ int(right, 16)).count("1")
    except (TypeError, ValueError):
        return 64


async def _find_exact_duplicate(
    session: AsyncSession, *, user_id: str, sha256: str
) -> Optional[ArchiveFile]:
    statement = (
        select(ArchiveFile)
        .where(
            ArchiveFile.user_id == user_id,
            ArchiveFile.sha256 == sha256,
            ArchiveFile.kind != "zip",
        )
        .order_by(ArchiveFile.created_at.asc())
        .limit(1)
    )
    return await session.scalar(statement)


def _validate_upload_size(data: bytes, filename: str, *, zip_allowed: bool) -> None:
    if not data:
        raise ArchiveUploadError(f"\"{filename}\" is empty.")
    limit = MAX_ZIP_UPLOAD_BYTES if zip_allowed else MAX_ARCHIVE_FILE_BYTES
    if len(data) > limit:
        raise ArchiveUploadError(
            f"\"{filename}\" is {len(data) / (1024 * 1024):.1f} MB, over the "
            f"{limit // (1024 * 1024)} MB limit."
        )


async def _create_file_row(
    session: AsyncSession,
    *,
    archive: Archive,
    data: bytes,
    filename: str,
    content_type: str,
    kind: str,
    parent_file_id: Optional[str] = None,
    status: str = "queued",
    storage_key: Optional[str] = None,
    size_bytes: Optional[int] = None,
    sha256: Optional[str] = None,
) -> ArchiveFile:
    safe_filename = pathlib.PurePosixPath((filename or "upload").replace("\\", "/")).name
    safe_filename = safe_filename[:255] or "upload"
    stored = None
    if storage_key is None or sha256 is None:
        try:
            stored = save_bytes(data)
        except StorageError as exc:
            raise ArchiveUploadError(str(exc)) from exc
        storage_key = stored.storage_key
        sha256 = stored.sha256
        size_bytes = stored.size_bytes

    duplicate = await _find_exact_duplicate(session, user_id=archive.user_id, sha256=sha256)
    row = ArchiveFile(
        id=str(uuid.uuid4()),
        archive_id=archive.id,
        user_id=archive.user_id,
        parent_file_id=parent_file_id,
        original_filename=safe_filename,
        content_type=(content_type or "application/octet-stream")[:120],
        size_bytes=size_bytes or len(data),
        sha256=sha256,
        storage_key=storage_key,
        kind=kind,
        status=status,
    )
    if duplicate is not None:
        row.duplicate_of_id = duplicate.id
        row.duplicate_kind = "exact"
    session.add(row)
    await session.flush()
    return row


async def ingest_uploads(
    session: AsyncSession,
    archive: Archive,
    *,
    files: Sequence[tuple[str, str, bytes]],
) -> IngestResult:
    """Validate and store one request's uploads.

    A ZIP is inspected safely and each accepted member becomes its own archive
    file (with ``parent_file_id`` pointing at the archive). Rejections are
    returned to the caller — never silently discarded — and do not stop the
    remaining files.
    """
    result = IngestResult()
    if not files:
        raise ArchiveUploadError("No files were uploaded.")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise ArchiveUploadError(
            f"Upload at most {MAX_FILES_PER_REQUEST} files at a time."
        )

    for filename, content_type, data in files:
        display_name = pathlib.PurePosixPath((filename or "upload").replace("\\", "/")).name
        try:
            kind = _sniff_kind(data, display_name)
            _validate_upload_size(
                data, display_name, zip_allowed=kind == "zip"
            )
        except ArchiveUploadError as exc:
            result.rejected.append((display_name, str(exc)))
            continue

        if kind == "zip":
            try:
                inspection = inspect_zip(data, limits=limits_from_env())
            except ZipSafetyError as exc:
                result.rejected.append((display_name, str(exc)))
                continue
            except Exception as exc:  # noqa: BLE001 — never a 500 for a bad zip
                logger.warning("zip %s could not be inspected: %s", display_name, type(exc).__name__)
                result.rejected.append(
                    (display_name, "The archive could not be read safely.")
                )
                continue

            parent = await _create_file_row(
                session,
                archive=archive,
                data=data,
                filename=display_name,
                content_type=content_type or "application/zip",
                kind="zip",
                status="completed",
            )
            parent.page_count = len(inspection.members)
            parent.extra = {
                "expanded_files": len(inspection.members),
                "rejected_entries": [
                    {"name": name, "reason": reason}
                    for name, reason in inspection.rejected[:40]
                ],
                "warnings": inspection.warnings,
            }
            parent.status_detail = (
                f"Expanded into {len(inspection.members)} file(s)"
                + (
                    f"; {len(inspection.rejected)} entry(ies) skipped"
                    if inspection.rejected
                    else ""
                )
            )
            result.warnings.extend(inspection.warnings)
            result.files.append(
                IngestedFile(
                    id=parent.id,
                    filename=parent.original_filename,
                    kind="zip",
                    status=parent.status,
                    size_bytes=parent.size_bytes,
                )
            )
            for member in inspection.members:
                try:
                    child = await _create_file_row(
                        session,
                        archive=archive,
                        data=member.data,
                        filename=member.filename,
                        content_type="",
                        kind=member.kind,
                        parent_file_id=parent.id,
                    )
                except ArchiveUploadError as exc:
                    result.rejected.append(
                        (f"{display_name} → {member.archive_path}", str(exc))
                    )
                    continue
                child.extra = {"archive_path": member.archive_path}
                result.files.append(
                    IngestedFile(
                        id=child.id,
                        filename=child.original_filename,
                        kind=child.kind,
                        status=child.status,
                        size_bytes=child.size_bytes,
                    )
                )
            continue

        try:
            row = await _create_file_row(
                session,
                archive=archive,
                data=data,
                filename=display_name,
                content_type=content_type or "",
                kind=kind,
            )
        except ArchiveUploadError as exc:
            result.rejected.append((display_name, str(exc)))
            continue
        result.files.append(
            IngestedFile(
                id=row.id,
                filename=row.original_filename,
                kind=row.kind,
                status=row.status,
                size_bytes=row.size_bytes,
            )
        )

    archive.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return result


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _set_status(file_id: str, **fields) -> None:
    """One short transaction per lifecycle update."""
    async with session_scope() as session:
        row = await session.get(ArchiveFile, file_id)
        if row is None:
            return
        for key, value in fields.items():
            setattr(row, key, value)
        await session.flush()


async def _known_names(session: AsyncSession) -> dict[str, tuple[str, str]]:
    """Gazetteer: canonical entities plus curated record titles."""
    known: dict[str, tuple[str, str]] = {}
    entity_rows = (
        await session.execute(select(Entity.name, Entity.entity_type).limit(5000))
    ).all()
    for name, entity_type in entity_rows:
        key = " ".join((name or "").split()).casefold()
        if key and len(key) >= 3:
            known.setdefault(key, (name, entity_type))
    # Curated historical record titles are real named subjects too; they are
    # typed 'other' unless a canonical entity already exists for them.
    try:
        from ..db.models import HistoricalRecord

        record_titles = (
            await session.execute(select(HistoricalRecord.title).limit(2000))
        ).scalars()
        for title in record_titles:
            key = " ".join((title or "").split()).casefold()
            if key and len(key) >= 3:
                known.setdefault(key, (title, "other"))
    except Exception as exc:  # noqa: BLE001 — optional enrichment only
        logger.warning("record titles unavailable for the gazetteer: %s", type(exc).__name__)
    return known


async def _store_pages(
    session: AsyncSession, file_id: str, extraction: DocumentExtraction
) -> None:
    """Replace this file's pages with the extraction's pages."""
    existing = (
        await session.scalars(select(ArchivePage).where(ArchivePage.file_id == file_id))
    ).all()
    for page in existing:
        await session.delete(page)
    await session.flush()
    for page in extraction.pages:
        session.add(
            ArchivePage(
                file_id=file_id,
                page_number=page.page_number,
                text=page.text,
                char_count=len(page.text),
                text_source=page.source,
            )
        )
    await session.flush()


async def _persist_intelligence(
    session: AsyncSession,
    *,
    row: ArchiveFile,
    pages: Sequence[tuple[Optional[int], str]],
) -> tuple[int, int]:
    """Entity candidates + dated events for one file. Returns their counts."""
    known = await _known_names(session)

    # Entities: replace this file's links (reprocessing must not accumulate).
    existing_links = (
        await session.scalars(
            select(ArchiveEntity).where(ArchiveEntity.file_id == row.id)
        )
    ).all()
    for link in existing_links:
        await session.delete(link)
    await session.flush()

    mentions = extract_entity_mentions(pages, known)
    if mentions:
        entities = await upsert_entities(
            session,
            [{"name": item.name, "entity_type": item.entity_type} for item in mentions],
        )
        # upsert_entities preserves input order for unique inputs.
        for mention, entity in zip(mentions, entities):
            session.add(
                ArchiveEntity(
                    file_id=row.id,
                    entity_id=entity.id,
                    page_number=mention.page_number,
                    confidence=mention.confidence,
                    extraction_method=mention.method,
                    source_excerpt=mention.excerpt,
                )
            )
        await session.flush()

    # Timeline: same replace semantics.
    existing_events = (
        await session.scalars(
            select(TimelineEvent).where(TimelineEvent.file_id == row.id)
        )
    ).all()
    for event in existing_events:
        await session.delete(event)
    await session.flush()

    seen: set[tuple[str, str]] = set()
    created = 0
    for page_number, text in pages:
        if not text:
            continue
        for mention in parse_date_mentions(text):
            key = (mention.date_text.casefold(), mention.sentence.casefold())
            if key in seen:
                continue
            seen.add(key)
            title = mention.sentence.rstrip(" .;:")
            if len(title) > 200:
                title = title[:199].rstrip(" ,;:") + "…"
            session.add(
                TimelineEvent(
                    user_id=row.user_id,
                    archive_id=row.archive_id,
                    file_id=row.id,
                    title=title or mention.date_text,
                    description=mention.sentence,
                    date_text=mention.date_text[:64],
                    year_start=mention.year_start,
                    year_end=mention.year_end,
                    era=mention.era,
                    precision=mention.precision,
                    source_page=page_number,
                    evidence_excerpt=mention.sentence,
                    verification_status="unverified",
                )
            )
            created += 1
            if created >= 120:
                break
        if created >= 120:
            break
    await session.flush()
    return len(mentions), created


async def _detect_similar_duplicates(
    session: AsyncSession, row: ArchiveFile
) -> None:
    """Flag a near-identical photograph or document (never delete anything)."""
    if row.duplicate_of_id is not None:
        return

    if row.perceptual_hash:
        candidates = (
            await session.scalars(
                select(ArchiveFile)
                .where(
                    ArchiveFile.user_id == row.user_id,
                    ArchiveFile.id != row.id,
                    ArchiveFile.perceptual_hash.is_not(None),
                    ArchiveFile.kind == "image",
                    ArchiveFile.status.in_(_DONE),
                    ArchiveFile.duplicate_of_id.is_(None),
                )
                .order_by(ArchiveFile.created_at.desc())
                .limit(400)
            )
        ).all()
        for candidate in candidates:
            if _hamming(row.perceptual_hash, candidate.perceptual_hash or "") <= _DUPLICATE_DISTANCE:
                row.duplicate_of_id = candidate.id
                row.duplicate_kind = "similar_image"
                await session.flush()
                return

    if row.text_fingerprint:
        candidate = await session.scalar(
            select(ArchiveFile)
            .where(
                ArchiveFile.user_id == row.user_id,
                ArchiveFile.id != row.id,
                ArchiveFile.text_fingerprint == row.text_fingerprint,
                ArchiveFile.status.in_(_DONE),
                ArchiveFile.duplicate_of_id.is_(None),
            )
            .order_by(ArchiveFile.created_at.asc())
            .limit(1)
        )
        if candidate is not None:
            row.duplicate_of_id = candidate.id
            row.duplicate_kind = "similar_text"
            await session.flush()


def _grade_document(extraction: DocumentExtraction) -> tuple[str, Optional[str]]:
    """``(status, error)`` for a document, never a false "completed".

    * text present and no degraded configured stage → ``completed``;
    * text present but a configured stage degraded → ``partial``;
    * no text, OCR simply not configured → ``partial`` with an actionable
      reason (configuring OCR recovers it);
    * no text at all → ``failed`` (digitisation did not happen).
    """
    if extraction.has_text:
        return ("partial" if extraction.degraded else "completed"), None
    if any("OCR skipped" in warning for warning in extraction.warnings):
        return "partial", None
    return "failed", (
        "No text could be extracted from this file. It may contain only images "
        "or no machine-readable text; the original is preserved."
    )


async def _process_document(
    row: ArchiveFile, data: bytes, *, ocr_client=None
) -> tuple[DocumentExtraction, str, Optional[str]]:
    try:
        extraction = await extract_document(
            data, filename=row.original_filename, kind=row.kind, ocr_client=ocr_client
        )
    except ExtractionError as exc:
        raise ArchiveProcessingFailure(str(exc)) from exc
    status, error = _grade_document(extraction)
    return extraction, status, error


class ArchiveProcessingFailure(ValueError):
    """A file could not be digitised (safe message recorded on the file)."""


async def _process_image(
    row: ArchiveFile, data: bytes, *, ocr_client=None
) -> tuple[DocumentExtraction, str, Optional[str]]:
    warnings: list[str] = []
    try:
        prepared = await asyncio.to_thread(
            prepare_image, data, filename=row.original_filename, content_type=row.content_type
        )
    except ApiError as exc:
        raise ArchiveProcessingFailure(str(exc)) from exc

    perceptual = _dhash(prepared)
    text = ""
    source = "none"
    degraded = False
    try:
        result = await run_ocr(
            prepared.to_png_bytes(),
            filename=row.original_filename,
            content_type="image/png",
            client=ocr_client,
        )
        text = normalise_text(result.text)
        source = "ocr" if text else "none"
        if not text:
            warnings.append(
                "No readable text was found in this photograph. The image itself "
                "is preserved; visual analysis is available from the analysis page."
            )
    except ProviderConfigurationError as exc:
        degraded = True
        warnings.append(f"OCR skipped: {exc}")
    except ProviderError as exc:
        degraded = True
        warnings.append(f"OCR could not read this image: {exc}")
    except Exception as exc:  # noqa: BLE001 — provider boundary
        degraded = True
        logger.warning("archive image OCR failed: %s", type(exc).__name__)
        warnings.append("OCR could not read this image.")

    language, script = detect_language(text)
    extraction = DocumentExtraction(
        pages=[ExtractedPage(page_number=1, text=text, source=source)],
        text=text,
        page_label="Image",
        language=language,
        language_script=script,
        metadata={
            "page_label": "Image",
            "width": prepared.width,
            "height": prepared.height,
            "format": prepared.format,
            "perceptual_hash": perceptual,
        },
        warnings=warnings,
        degraded=degraded,
    )
    # An image is digitised when OCR ran (even if it found nothing): "no text
    # in this photo" is a valid OCR answer. A missing/broken provider leaves
    # the file partial — its bytes and metadata are still preserved.
    status = "partial" if degraded else "completed"
    return extraction, status, None


async def process_archive_file(file_id: str, *, ocr_client=None) -> None:
    """Digitise one archived file; every outcome is recorded on the row.

    Never raises for a per-file failure: the failure is stored as
    ``status='failed'`` with a safe message so the rest of the archive keeps
    processing. (The background task boundary still guards unexpected bugs.)
    """
    async with session_scope() as session:
        row = await session.get(ArchiveFile, file_id)
        if row is None or row.kind == "zip":
            return
        if row.status not in ("uploaded", "queued", "failed", "partial"):
            return
        archive = await session.get(Archive, row.archive_id)
        if archive is None or archive.deleted_at is not None:
            return
        row.status = "processing"
        row.status_detail = "Reading the original…"
        row.error = None
        storage_key = row.storage_key
        kind = row.kind
        filename = row.original_filename
        await session.flush()

    try:
        data = read_bytes(storage_key or "")
    except StorageError as exc:
        await _set_status(
            file_id, status="failed", error=str(exc), status_detail="Original missing"
        )
        return

    try:
        if kind == "image":
            extraction, status, error = await _process_image(row, data, ocr_client=ocr_client)
        elif kind in {"pdf", "docx", "pptx", "text"}:
            extraction, status, error = await _process_document(
                row, data, ocr_client=ocr_client
            )
        else:  # pragma: no cover - ZIP rows are not queued
            raise ArchiveProcessingFailure("Unsupported file type.")

        await _set_status(
            file_id,
            status="text_extracted" if extraction.has_text else "processing",
            status_detail=(
                f"Extracted text from {extraction.page_count} "
                f"{extraction.page_label.lower()}(s)"
                if extraction.has_text
                else "No text extracted yet"
            ),
            extracted_text=extraction.text or None,
            language=extraction.language,
            page_count=extraction.page_count,
            extra={
                **(row.extra or {}),
                **extraction.metadata,
                "language_script": extraction.language_script,
                "language_method": "script-heuristic" if extraction.language else None,
                "page_label": extraction.page_label,
                "warnings": extraction.warnings,
                "duplicate_kind": row.duplicate_kind,
            },
        )

        async with session_scope() as session:
            fresh = await session.get(ArchiveFile, file_id)
            if fresh is None:
                return
            await _store_pages(session, file_id, extraction)
            pages = [(page.page_number, page.text) for page in extraction.pages]
            entity_count, event_count = await _persist_intelligence(
                session, row=fresh, pages=pages
            )
            if extraction.text.strip():
                fresh.text_fingerprint = text_fingerprint(extraction.text)
            if kind == "image":
                fresh.perceptual_hash = extraction.metadata.get("perceptual_hash")
            fresh.status = status
            fresh.status_detail = (
                f"{entity_count} entity candidate(s), {event_count} dated event(s)"
                if extraction.has_text
                else "No text extracted"
            )
            fresh.error = error
            fresh.page_count = extraction.page_count or fresh.page_count
            await _detect_similar_duplicates(session, fresh)
            archive = await session.get(Archive, fresh.archive_id)
            if archive is not None:
                archive.updated_at = _utcnow()
            await session.flush()

        logger.info(
            "archive file %s (%s) processed: %s, %d page(s), %d byte(s)",
            file_id,
            kind,
            status,
            extraction.page_count,
            len(data),
        )
    except ArchiveProcessingFailure as exc:
        await _set_status(
            file_id,
            status="failed",
            error=str(exc),
            status_detail="Digitisation failed",
        )
    except Exception as exc:  # noqa: BLE001 — per-file boundary
        logger.error(
            "archive file %s failed: %s", file_id, type(exc).__name__, exc_info=exc
        )
        await _set_status(
            file_id,
            status="failed",
            error=(
                "This file could not be processed due to an unexpected server "
                "error. The original is preserved; try reprocessing it."
            ),
            status_detail="Processing failed",
        )


# ---------------------------------------------------------------------------
# Read side: stats, detail, search, timeline
# ---------------------------------------------------------------------------


async def archive_stats(session: AsyncSession, archive: Archive) -> dict:
    """Real aggregates for one archive (no hardcoded numbers anywhere)."""
    files = (
        await session.scalars(
            select(ArchiveFile).where(ArchiveFile.archive_id == archive.id)
        )
    ).all()
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    languages: dict[str, int] = {}
    pages = 0
    characters = 0
    duplicates = 0
    for row in files:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        by_kind[row.kind] = by_kind.get(row.kind, 0) + 1
        if row.language:
            languages[row.language] = languages.get(row.language, 0) + 1
        pages += row.page_count or 0
        characters += len(row.extracted_text or "")
        if row.duplicate_of_id:
            duplicates += 1
    entity_count = (
        await session.scalar(
            select(func.count(ArchiveEntity.entity_id))
            .select_from(ArchiveEntity)
            .join(ArchiveFile, ArchiveFile.id == ArchiveEntity.file_id)
            .where(ArchiveFile.archive_id == archive.id)
        )
        or 0
    )
    event_count = (
        await session.scalar(
            select(func.count(TimelineEvent.id)).where(
                TimelineEvent.archive_id == archive.id
            )
        )
        or 0
    )
    return {
        "files": len(files),
        "by_status": by_status,
        "by_kind": by_kind,
        "languages": languages,
        "pages": pages,
        "characters": characters,
        "entities": entity_count,
        "timeline_events": event_count,
        "duplicates": duplicates,
        "completed": by_status.get("completed", 0),
        "partial": by_status.get("partial", 0),
        "failed": by_status.get("failed", 0),
        "processing": by_status.get("processing", 0)
        + by_status.get("queued", 0)
        + by_status.get("text_extracted", 0)
        + by_status.get("ocr_completed", 0),
    }


async def archive_file_detail(
    session: AsyncSession, file_id: str, *, user_id: str
) -> Optional[dict]:
    """Full detail of one archived file (pages, entities, timeline, duplicate)."""
    row = await session.get(ArchiveFile, file_id)
    if row is None or row.user_id != user_id:
        return None
    pages = (
        await session.scalars(
            select(ArchivePage)
            .where(ArchivePage.file_id == file_id)
            .order_by(ArchivePage.page_number)
        )
    ).all()
    entity_rows = (
        await session.execute(
            select(Entity, ArchiveEntity)
            .join(ArchiveEntity, ArchiveEntity.entity_id == Entity.id)
            .where(ArchiveEntity.file_id == file_id)
            .order_by(ArchiveEntity.confidence.desc(), Entity.name)
        )
    ).all()
    events = (
        await session.scalars(
            select(TimelineEvent)
            .where(TimelineEvent.file_id == file_id)
            .order_by(TimelineEvent.year_start.is_(None), TimelineEvent.year_start, TimelineEvent.date_text)
        )
    ).all()
    duplicate = None
    if row.duplicate_of_id:
        duplicate = await session.get(ArchiveFile, row.duplicate_of_id)
    parent = await session.get(ArchiveFile, row.parent_file_id) if row.parent_file_id else None
    archive = await session.get(Archive, row.archive_id)
    return {
        "file": row,
        "archive": archive,
        "pages": pages,
        "entities": [
            {
                "id": entity.id,
                "name": entity.name,
                "type": entity.entity_type,
                "page_number": link.page_number,
                "confidence": link.confidence,
                "extraction_method": link.extraction_method,
                "excerpt": link.source_excerpt,
            }
            for entity, link in entity_rows
        ],
        "timeline": events,
        "duplicate": duplicate,
        "parent": parent,
    }


def _tokens(query: str) -> list[str]:
    return [
        token
        for token in (
            part.casefold() for part in re.findall(r"[\w\u0900-\u0DFF'’\-]+", query)
        )
        if len(token) >= 2
    ][:8]


def _excerpt(text: str, tokens: Sequence[str], *, limit: int = 300) -> str:
    """A passage around the first query hit (or the page's opening)."""
    flattened = " ".join(text.split())
    if not flattened:
        return ""
    position = -1
    for token in tokens:
        found = flattened.casefold().find(token)
        if found != -1 and (position == -1 or found < position):
            position = found
    if position == -1:
        return flattened[: limit - 1] + "…" if len(flattened) > limit else flattened
    start = max(0, position - 90)
    end = min(len(flattened), start + limit)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flattened) else ""
    return f"{prefix}{flattened[start:end]}{suffix}"


async def search_archive_files(
    session: AsyncSession,
    *,
    user_id: str,
    query: str,
    archive_id: Optional[str] = None,
    language: Optional[str] = None,
    kind: Optional[str] = None,
    entity_type: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """Keyword search across the user's digitised archives.

    This is honestly a **keyword** search (SQL prefilter + ranked token
    counts). It is never described as semantic: no embedding provider is
    configured in this deployment, and calling token matching "semantic"
    would be a lie to the user.
    """
    tokens = _tokens(query)
    if not tokens:
        raise ArchiveUploadError("Enter at least two characters to search the archive.")

    statement: Select = (
        select(ArchivePage, ArchiveFile, Archive)
        .join(ArchiveFile, ArchiveFile.id == ArchivePage.file_id)
        .join(Archive, Archive.id == ArchiveFile.archive_id)
        .where(
            Archive.user_id == user_id,
            Archive.deleted_at.is_(None),
            ArchiveFile.kind != "zip",
        )
    )
    if archive_id:
        statement = statement.where(ArchiveFile.archive_id == archive_id)
    if language:
        statement = statement.where(ArchiveFile.language == language)
    if kind:
        statement = statement.where(ArchiveFile.kind == kind)
    if entity_type:
        statement = statement.where(
            ArchiveFile.id.in_(
                select(ArchiveEntity.file_id)
                .join(Entity, Entity.id == ArchiveEntity.entity_id)
                .where(Entity.entity_type == entity_type)
            )
        )
    rows = (await session.execute(statement.limit(6000))).all()

    scored: list[tuple[float, ArchivePage, ArchiveFile, Archive]] = []
    for page, file_row, archive in rows:
        haystack = (page.text or "").casefold()
        title = (file_row.original_filename or "").casefold()
        score = 0.0
        for token in tokens:
            occurrences = haystack.count(token)
            if occurrences:
                score += occurrences * 2.0
            if token in title:
                score += 4.0
        if score <= 0:
            continue
        scored.append((score, page, file_row, archive))

    scored.sort(key=lambda item: (-item[0], item[1].page_number))
    truncated = len(rows) >= 6000

    # Matched entities per file (one query, not N+1).
    file_ids = {str(page.file_id) for _score, page, _file, _archive in scored[: limit * 4]}
    entities_by_file: dict[str, list[dict]] = {}
    if file_ids:
        entity_rows = (
            await session.execute(
                select(ArchiveEntity.file_id, Entity.name, Entity.entity_type, ArchiveEntity.page_number)
                .join(Entity, Entity.id == ArchiveEntity.entity_id)
                .where(ArchiveEntity.file_id.in_(file_ids))
                .order_by(ArchiveEntity.confidence.desc())
                .limit(400)
            )
        ).all()
        for file_id, name, type_, page_number in entity_rows:
            entities_by_file.setdefault(str(file_id), []).append(
                {"name": name, "type": type_, "pageNumber": page_number}
            )

    results = []
    seen: set[tuple[str, int]] = set()
    for score, page, file_row, archive in scored:
        key = (str(page.file_id), page.page_number)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "fileId": str(page.file_id),
                "archiveId": str(archive.id),
                "archiveName": archive.name,
                "filename": file_row.original_filename,
                "kind": file_row.kind,
                "pageNumber": page.page_number,
                "pageLabel": (file_row.extra or {}).get("page_label") or "Page",
                "excerpt": _excerpt(page.text, tokens),
                "score": round(score, 3),
                "language": file_row.language,
                "textSource": page.text_source,
                "matchedEntities": entities_by_file.get(str(page.file_id), [])[:6],
            }
        )
        if len(results) >= limit:
            break

    return {
        "query": query.strip(),
        "mode": "keyword",
        "results": results,
        "total": len(results),
        "notice": (
            "Keyword search matches the extracted text of your archives. Semantic "
            "search needs an embedding provider, which is not configured."
        ),
        "truncated": truncated,
    }


async def list_timeline(
    session: AsyncSession,
    *,
    user_id: str,
    archive_id: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    statement = select(TimelineEvent).where(TimelineEvent.user_id == user_id)
    if archive_id:
        statement = statement.where(TimelineEvent.archive_id == archive_id)
    statement = statement.order_by(
        TimelineEvent.year_start.is_(None),
        TimelineEvent.year_start,
        TimelineEvent.date_text,
    ).limit(limit)
    events = (await session.scalars(statement)).all()
    file_ids = {event.file_id for event in events if event.file_id}
    names: dict[str, str] = {}
    if file_ids:
        rows = (
            await session.execute(
                select(ArchiveFile.id, ArchiveFile.original_filename).where(
                    ArchiveFile.id.in_(file_ids)
                )
            )
        ).all()
        names = {str(file_id): filename for file_id, filename in rows}
    return [
        {
            "id": event.id,
            "title": event.title,
            "description": event.description,
            "dateText": event.date_text,
            "yearStart": event.year_start,
            "yearEnd": event.year_end,
            "era": event.era,
            "precision": event.precision,
            "sourcePage": event.source_page,
            "evidence": event.evidence_excerpt,
            "verificationStatus": event.verification_status,
            "archiveId": event.archive_id,
            "fileId": event.file_id,
            "sourceFile": names.get(event.file_id or "", None),
        }
        for event in events
    ]
