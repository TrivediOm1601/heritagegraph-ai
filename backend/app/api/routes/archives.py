"""Heritage Archive Studio routes.

Every handler authenticates and owner-scopes its query. Another account's
archive or file id is a ``404`` — identical to a nonexistent one — so ids
cannot be probed. Uploads are validated before anything is stored, ZIPs are
inspected by :mod:`app.services.zip_service`, and processing continues in the
background so the upload response is immediate.
"""

from __future__ import annotations

from typing import Annotated, Optional
from urllib.parse import quote

from sqlalchemy import select

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
)

from ...core.config import ConfigurationError
from ...core.errors import (
    ApiError,
    ConflictApiError,
    DependencyUnavailableError,
    ForbiddenError,
    NotFoundError,
    PayloadTooLargeError,
    UnprocessableEntityError,
    UnsupportedMediaTypeError,
    error_response,
)
from ...core.logging import get_logger
from ...core.security import (
    CurrentUser,
    limit_archive,
    limit_export,
    require_user,
    verify_same_origin,
)
from ...db.models import (
    Archive,
    ArchiveEntity,
    ArchiveFile,
    Entity,
    TimelineEvent,
)
from ...db.session import RepositoryError, session_scope
from ...schemas.story import (
    SiteStoriesResponse,
    StoryAskRequest,
    StoryAskResponse,
    StoryDetail,
    StoryModerationRequest,
    StorySubmissionRequest,
    StorySubmissionResponse,
    StorySummary,
)
from ...schemas.archive import (
    ArchiveCreateRequest,
    ArchiveDetailResponse,
    ArchiveFileDetailResponse,
    ArchiveFileItem,
    ArchiveListResponse,
    ArchiveSearchResponse,
    ArchiveStats,
    ArchiveSummary,
    ArchiveUpdateRequest,
    ArchiveUploadResponse,
    ExtractedPageItem,
    FileEntityItem,
    HeritageRouteRequest,
    HeritageRouteResponse,
    HeritageSiteItem,
    HeritageSitesResponse,
    ReproduceResponse,
    RejectedUpload,
    TimelineEventItem,
    TimelineResponse,
)
from ...services.archive_report_service import (
    archive_report_filename,
    render_archive_report,
)
from ...services.archive_service import (
    MAX_FILES_PER_REQUEST,
    MAX_ZIP_UPLOAD_BYTES,
    ArchiveUploadError,
    archive_file_detail,
    archive_stats,
    create_archive,
    get_archive,
    ingest_uploads,
    list_archives,
    list_timeline,
    process_archive_file,
    search_archive_files,
    soft_delete_archive,
    update_archive,
)
from ...services.heritage_service import (
    DATASET_LABEL,
    HeritageFilters,
    build_route,
    heritage_facets,
    heritage_summary,
    list_sites,
    sites_payload,
)
from ...services.story_service import (
    StoryFilters,
    ask_stories,
    featured_stories_by_site,
    moderate_story,
    moderator_emails,
    site_stories,
    story_categories_by_site,
    story_counts_by_site,
    story_detail,
    submit_story,
)
from ...services.storage_service import StorageError, read_bytes

router = APIRouter(tags=["archives"])

logger = get_logger("archive.routes")

ARCHIVE_UNAVAILABLE = (
    "The Heritage Archive Studio is unavailable: the database could not be "
    "reached. Please try again."
)
REPORT_UNAVAILABLE = (
    "The archive report could not be generated. Please try again."
)
#: Whole-request bound: many large files must not exhaust server memory.
MAX_REQUEST_BYTES = 150 * 1024 * 1024

_ID_CONSTRAINTS: dict[str, object] = {
    "min_length": 1,
    "max_length": 128,
    "pattern": r"^[^\s]+$",
}

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}

#: Only these types are ever served back to a browser, and always as a
#: download — never inline (an uploaded file must not become active content).
_DOWNLOAD_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/zip",
    "text/plain",
    "text/markdown",
}


def _summary(archive: Archive, stats: dict) -> ArchiveSummary:
    return ArchiveSummary(
        id=archive.id,
        name=archive.name,
        description=archive.description,
        region=archive.region,
        tags=[str(tag) for tag in (archive.tags or [])],
        createdAt=archive.created_at,
        updatedAt=archive.updated_at,
        stats=ArchiveStats(**stats),
    )


def _file_item(row: ArchiveFile) -> ArchiveFileItem:
    extra = row.extra if isinstance(row.extra, dict) else {}
    warnings = [str(item) for item in (extra.get("warnings") or [])][:8]
    return ArchiveFileItem(
        id=row.id,
        archiveId=row.archive_id,
        parentFileId=row.parent_file_id,
        filename=row.original_filename,
        contentType=row.content_type,
        sizeBytes=row.size_bytes,
        kind=row.kind,
        status=row.status,
        statusDetail=row.status_detail,
        error=row.error,
        language=row.language,
        languageScript=extra.get("language_script"),
        pageCount=row.page_count or 0,
        pageLabel=extra.get("page_label") or "Page",
        duplicateOfId=row.duplicate_of_id,
        duplicateKind=row.duplicate_kind,
        warnings=warnings,
        archivePath=extra.get("archive_path"),
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def _timeline_item(event: TimelineEvent, source_file: Optional[str] = None) -> TimelineEventItem:
    return TimelineEventItem(
        id=event.id,
        title=event.title,
        description=event.description,
        dateText=event.date_text,
        yearStart=event.year_start,
        yearEnd=event.year_end,
        era=event.era,
        precision=event.precision,
        sourcePage=event.source_page,
        evidence=event.evidence_excerpt,
        verificationStatus=event.verification_status,
        archiveId=event.archive_id,
        fileId=event.file_id,
        sourceFile=source_file,
    )


async def _detail(session, archive: Archive) -> ArchiveDetailResponse:
    stats = await archive_stats(session, archive)
    files = (
        await session.execute(
            select(ArchiveFile)
            .where(ArchiveFile.archive_id == archive.id)
            .order_by(ArchiveFile.created_at.asc())
        )
    ).scalars().all()
    summary = _summary(archive, stats)
    return ArchiveDetailResponse(
        **summary.model_dump(by_alias=False),
        files=[_file_item(row) for row in files],
    )


async def _load_owned_archive(session, archive_id: str, user: CurrentUser) -> Archive:
    archive = await get_archive(session, archive_id, user_id=user.id)
    if archive is None:
        raise NotFoundError("No archive exists with that id.")
    return archive


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError(
                f"\"{file.filename or 'file'}\" is over the "
                f"{max_bytes // (1024 * 1024)} MB limit."
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/archives",
    response_model=ArchiveDetailResponse,
    status_code=201,
    summary="Create a heritage archive",
    responses={
        401: error_response("Sign in to create an archive."),
        422: error_response("The archive details are invalid."),
        503: error_response("The database is unavailable."),
    },
)
async def create_archive_route(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    payload: ArchiveCreateRequest,
) -> ArchiveDetailResponse:
    verify_same_origin(request)
    try:
        async with session_scope() as session:
            archive = await create_archive(
                session,
                user_id=user.id,
                name=payload.name,
                description=payload.description,
                region=payload.region,
                tags=payload.tags,
            )
            return await _detail(session, archive)
    except ArchiveUploadError as exc:
        raise UnprocessableEntityError(str(exc)) from exc
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(ARCHIVE_UNAVAILABLE) from exc


@router.get(
    "/archives",
    response_model=ArchiveListResponse,
    summary="List this account's archives",
    responses={
        401: error_response("Sign in to see your archives."),
        503: error_response("The database is unavailable."),
    },
)
async def list_archives_route(
    user: Annotated[CurrentUser, Depends(require_user)],
) -> ArchiveListResponse:
    try:
        async with session_scope() as session:
            archives = await list_archives(session, user_id=user.id)
            items = []
            for archive in archives:
                items.append(_summary(archive, await archive_stats(session, archive)))
            return ArchiveListResponse(items=items, total=len(items))
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(ARCHIVE_UNAVAILABLE) from exc


@router.get(
    "/archives/{archive_id}",
    response_model=ArchiveDetailResponse,
    summary="One archive with its files and statistics",
    responses={
        401: error_response("Sign in to read an archive."),
        404: error_response("No archive exists with that id."),
        503: error_response("The database is unavailable."),
    },
)
async def get_archive_route(
    user: Annotated[CurrentUser, Depends(require_user)],
    archive_id: Annotated[str, Path(**_ID_CONSTRAINTS)],
) -> ArchiveDetailResponse:
    try:
        async with session_scope() as session:
            archive = await _load_owned_archive(session, archive_id, user)
            return await _detail(session, archive)
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(ARCHIVE_UNAVAILABLE) from exc


@router.patch(
    "/archives/{archive_id}",
    response_model=ArchiveDetailResponse,
    summary="Rename or re-describe an archive",
)
async def update_archive_route(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    archive_id: Annotated[str, Path(**_ID_CONSTRAINTS)],
    payload: ArchiveUpdateRequest,
) -> ArchiveDetailResponse:
    verify_same_origin(request)
    try:
        async with session_scope() as session:
            archive = await _load_owned_archive(session, archive_id, user)
            archive = await update_archive(
                session,
                archive,
                name=payload.name,
                description=payload.description,
                region=payload.region,
                tags=payload.tags,
            )
            return await _detail(session, archive)
    except ArchiveUploadError as exc:
        raise UnprocessableEntityError(str(exc)) from exc
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(ARCHIVE_UNAVAILABLE) from exc


@router.delete(
    "/archives/{archive_id}",
    status_code=204,
    summary="Remove an archive from the workspace",
    description=(
        "Hides the archive and everything in it. The original files stay on "
        "the server (recoverable by an operator); nothing is served once it "
        "is removed."
    ),
)
async def delete_archive_route(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    archive_id: Annotated[str, Path(**_ID_CONSTRAINTS)],
) -> Response:
    verify_same_origin(request)
    try:
        async with session_scope() as session:
            archive = await _load_owned_archive(session, archive_id, user)
            await soft_delete_archive(session, archive)
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(ARCHIVE_UNAVAILABLE) from exc
    return Response(status_code=204)


@router.post(
    "/archives/{archive_id}/files",
    response_model=ArchiveUploadResponse,
    status_code=202,
    summary="Upload images, documents or a ZIP into an archive",
    description=(
        "Accepts multiple files (multipart field `files`). Supported images, "
        "PDF, DOCX, PPTX, TXT and Markdown are queued for extraction; a ZIP "
        "is inspected safely and each accepted member becomes its own file. "
        "Rejected files/entries are returned with their reason."
    ),
    responses={
        401: error_response("Sign in to upload."),
        404: error_response("No archive exists with that id."),
        413: error_response("An upload exceeds the size limit."),
        415: error_response("No upload is a supported file type."),
        422: error_response("The upload is missing or empty."),
        429: error_response("Too many uploads; please wait."),
        503: error_response("The database or storage is unavailable."),
    },
)
async def upload_files_route(
    request: Request,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_user)],
    archive_id: Annotated[str, Path(**_ID_CONSTRAINTS)],
    files: Annotated[list[UploadFile], File(description="One or more archive files.")],
) -> ArchiveUploadResponse:
    verify_same_origin(request)
    limit_archive.hit(f"archive-upload:{user.id}")

    if not files:
        raise UnprocessableEntityError("No files were uploaded.")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise UnprocessableEntityError(
            f"Upload at most {MAX_FILES_PER_REQUEST} files at a time."
        )

    payloads: list[tuple[str, str, bytes]] = []
    total = 0
    for upload in files:
        data = await _read_upload(upload, MAX_ZIP_UPLOAD_BYTES)
        total += len(data)
        if total > MAX_REQUEST_BYTES:
            raise PayloadTooLargeError(
                "This upload is larger than the 150 MB per-request limit."
            )
        if not data:
            continue
        payloads.append(
            (
                upload.filename or "upload",
                upload.content_type or "",
                data,
            )
        )
    if not payloads:
        raise UnprocessableEntityError("Every uploaded file is empty.")

    queued_ids: list[str] = []
    try:
        async with session_scope() as session:
            archive = await _load_owned_archive(session, archive_id, user)
            result = await ingest_uploads(session, archive, files=payloads)
            if not result.files:
                reason = result.rejected[0][1] if result.rejected else "Unsupported file."
                raise UnsupportedMediaTypeError(
                    "No file could be accepted. " + reason
                )
            queued_ids = result.queued_ids
            accepted_rows = await _rows(
                session, [item.id for item in result.files]
            )
            accepted_payload = [_file_item(row) for row in accepted_rows]
            response = ArchiveUploadResponse(
                accepted=accepted_payload,
                rejected=[
                    RejectedUpload(filename=name, reason=reason)
                    for name, reason in result.rejected[:40]
                ],
                warnings=result.warnings[:10],
                message=(
                    f"{len(result.files)} file(s) accepted; "
                    f"{len(queued_ids)} queued for text extraction."
                ),
            )
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(ARCHIVE_UNAVAILABLE) from exc
    except StorageError as exc:
        raise DependencyUnavailableError(str(exc)) from exc

    for file_id in queued_ids:
        background.add_task(process_archive_file, file_id)
    logger.info("archive %s: %d file(s) accepted, %d queued", archive_id, len(response.accepted), len(queued_ids))
    return response


async def _rows(session, ids: list[str]) -> list[ArchiveFile]:
    if not ids:
        return []
    rows = (
        await session.execute(
            select(ArchiveFile)
            .where(ArchiveFile.id.in_(ids))
            .order_by(ArchiveFile.created_at.asc())
        )
    ).scalars().all()
    return list(rows)


@router.get(
    "/archive-files/{file_id}",
    response_model=ArchiveFileDetailResponse,
    summary="Extracted text, entities, timeline and duplicate status of one file",
    responses={
        401: error_response("Sign in to read an archived file."),
        404: error_response("No archived file exists with that id."),
        503: error_response("The database is unavailable."),
    },
)
async def archive_file_detail_route(
    user: Annotated[CurrentUser, Depends(require_user)],
    file_id: Annotated[str, Path(**_ID_CONSTRAINTS)],
    pages_limit: Annotated[int, Query(ge=1, le=400, alias="pagesLimit")] = 120,
) -> ArchiveFileDetailResponse:
    try:
        async with session_scope() as session:
            detail = await archive_file_detail(session, file_id, user_id=user.id)
            if detail is None:
                raise NotFoundError("No archived file exists with that id.")
            row: ArchiveFile = detail["file"]
            archive = detail["archive"]
            page_rows = detail["pages"]
            limitations: list[str] = []
            if row.kind == "image":
                limitations.append(
                    "OCR extracts text only. The archive does not identify the "
                    "monument or object in a photograph; use the image-analysis "
                    "page for a visual candidate."
                )
            if row.kind == "zip":
                limitations.append(
                    "This is the uploaded ZIP container. Its members are listed "
                    "separately in the archive."
                )
            limitations.append(
                "Language is a script heuristic, not a confident language detector."
            )
            if row.duplicate_of_id:
                limitations.append(
                    "This file is flagged as a possible duplicate for review. It "
                    "has not been deleted or merged."
                )
            if row.extra and isinstance(row.extra, dict) and row.extra.get("rejected_entries"):
                limitations.append(
                    f"{len(row.extra['rejected_entries'])} archive entry(ies) were "
                    "skipped for safety; see the file warnings."
                )
            if len(page_rows) > pages_limit:
                limitations.append(
                    f"Only the first {pages_limit} of {len(page_rows)} pages are "
                    "shown here."
                )

            return ArchiveFileDetailResponse(
                file=_file_item(row),
                archiveId=row.archive_id,
                archiveName=archive.name if archive else "",
                pages=[
                    ExtractedPageItem(
                        pageNumber=page.page_number,
                        text=page.text,
                        charCount=page.char_count,
                        textSource=page.text_source,
                    )
                    for page in page_rows[:pages_limit]
                ],
                entities=[FileEntityItem(**entity) for entity in detail["entities"]],
                timeline=[
                    _timeline_item(event, row.original_filename)
                    for event in detail["timeline"][:120]
                ],
                duplicateOf=(
                    _file_item(detail["duplicate"]) if detail["duplicate"] else None
                ),
                parentFile=(
                    _file_item(detail["parent"]) if detail["parent"] else None
                ),
                limitations=limitations,
            )
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(ARCHIVE_UNAVAILABLE) from exc


@router.get("/archive-files/{file_id}/download", summary="Download the preserved original")
async def download_archive_file(
    user: Annotated[CurrentUser, Depends(require_user)],
    file_id: Annotated[str, Path(**_ID_CONSTRAINTS)],
) -> Response:
    try:
        async with session_scope() as session:
            row = await session.get(ArchiveFile, file_id)
            if row is None or row.user_id != user.id:
                raise NotFoundError("No archived file exists with that id.")
            storage_key = row.storage_key
            filename = row.original_filename
            content_type = row.content_type
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(ARCHIVE_UNAVAILABLE) from exc

    if not storage_key:
        raise NotFoundError("The original for this file is not stored.")
    try:
        payload = read_bytes(storage_key)
    except StorageError as exc:
        raise NotFoundError(str(exc)) from exc

    media_type = content_type if content_type in _DOWNLOAD_TYPES else "application/octet-stream"
    if media_type.startswith("image/"):
        media_type = content_type
    safe_name = quote(filename, safe="")
    return Response(
        content=payload,
        media_type=media_type or "application/octet-stream",
        headers={
            **_PRIVATE_HEADERS,
            "Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}",
        },
    )


@router.post(
    "/archive-files/{file_id}/reprocess",
    response_model=ReproduceResponse,
    status_code=202,
    summary="Reprocess a failed or partial file",
)
async def reprocess_file(
    request: Request,
    background: BackgroundTasks,
    user: Annotated[CurrentUser, Depends(require_user)],
    file_id: Annotated[str, Path(**_ID_CONSTRAINTS)],
) -> ReproduceResponse:
    verify_same_origin(request)
    limit_archive.hit(f"archive-reprocess:{user.id}")
    try:
        async with session_scope() as session:
            row = await session.get(ArchiveFile, file_id)
            if row is None or row.user_id != user.id:
                raise NotFoundError("No archived file exists with that id.")
            if row.kind == "zip":
                raise UnprocessableEntityError(
                    "A ZIP container is expanded at upload; reprocess its members "
                    "individually."
                )
            if row.status in ("queued", "processing"):
                raise UnprocessableEntityError(
                    "This file is already queued or being processed."
                )
            row.status = "queued"
            row.status_detail = "Queued for reprocessing"
            row.error = None
            await session.flush()
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(ARCHIVE_UNAVAILABLE) from exc

    background.add_task(process_archive_file, file_id)
    return ReproduceResponse(
        fileId=file_id,
        status="queued",
        message="The file was queued for reprocessing.",
    )


@router.delete(
    "/archive-files/{file_id}",
    status_code=204,
    summary="Remove one file from its archive",
)
async def delete_archive_file(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    file_id: Annotated[str, Path(**_ID_CONSTRAINTS)],
) -> Response:
    verify_same_origin(request)
    try:
        async with session_scope() as session:
            row = await session.get(ArchiveFile, file_id)
            if row is None or row.user_id != user.id:
                raise NotFoundError("No archived file exists with that id.")
            await session.delete(row)
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(ARCHIVE_UNAVAILABLE) from exc
    return Response(status_code=204)


@router.get(
    "/archive-search",
    response_model=ArchiveSearchResponse,
    summary="Search the extracted text of all your archives",
    description=(
        "Keyword search across your own archives, with page-level excerpts, "
        "matched entities and filters. The response states its mode honestly: "
        "no embedding provider is configured, so this is not semantic search."
    ),
)
async def archive_search_route(
    user: Annotated[CurrentUser, Depends(require_user)],
    q: Annotated[str, Query(min_length=2, max_length=200, description="Keywords to find.")],
    archive_id: Annotated[Optional[str], Query(alias="archiveId", max_length=128)] = None,
    language: Annotated[Optional[str], Query(max_length=32)] = None,
    kind: Annotated[
        Optional[str],
        Query(pattern="^(image|pdf|docx|pptx|text)$"),
    ] = None,
    entity_type: Annotated[
        Optional[str],
        Query(alias="entityType", pattern="^(person|place|event|date|other)$"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ArchiveSearchResponse:
    try:
        async with session_scope() as session:
            if archive_id:
                await _load_owned_archive(session, archive_id, user)
            result = await search_archive_files(
                session,
                user_id=user.id,
                query=q,
                archive_id=archive_id,
                language=language,
                kind=kind,
                entity_type=entity_type,
                limit=limit,
            )
            return ArchiveSearchResponse(**result)
    except ArchiveUploadError as exc:
        raise UnprocessableEntityError(str(exc)) from exc
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(ARCHIVE_UNAVAILABLE) from exc


@router.get(
    "/archives/{archive_id}/search",
    response_model=ArchiveSearchResponse,
    summary="Search one archive",
)
async def archive_scoped_search_route(
    user: Annotated[CurrentUser, Depends(require_user)],
    archive_id: Annotated[str, Path(**_ID_CONSTRAINTS)],
    q: Annotated[str, Query(min_length=2, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ArchiveSearchResponse:
    try:
        async with session_scope() as session:
            await _load_owned_archive(session, archive_id, user)
            result = await search_archive_files(
                session,
                user_id=user.id,
                query=q,
                archive_id=archive_id,
                limit=limit,
            )
            return ArchiveSearchResponse(**result)
    except ArchiveUploadError as exc:
        raise UnprocessableEntityError(str(exc)) from exc
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(ARCHIVE_UNAVAILABLE) from exc


@router.get(
    "/archives/{archive_id}/timeline",
    response_model=TimelineResponse,
    summary="Dated events extracted from one archive",
)
async def archive_timeline_route(
    user: Annotated[CurrentUser, Depends(require_user)],
    archive_id: Annotated[str, Path(**_ID_CONSTRAINTS)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> TimelineResponse:
    try:
        async with session_scope() as session:
            await _load_owned_archive(session, archive_id, user)
            events = await list_timeline(
                session, user_id=user.id, archive_id=archive_id, limit=limit
            )
            return TimelineResponse(
                events=[TimelineEventItem(**event) for event in events],
                total=len(events),
                limitations=[
                    "Dates are reproduced as written in the source; approximate "
                    "and century dates are not converted into exact years.",
                    "Events are extraction candidates and are not verified "
                    "historical claims.",
                ],
            )
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(ARCHIVE_UNAVAILABLE) from exc


@router.get(
    "/archives/{archive_id}/report.pdf",
    summary="Research report for one archive",
    responses={
        401: error_response("Sign in to export."),
        404: error_response("No archive exists with that id."),
        429: error_response("Too many exports; please wait."),
    },
)
async def archive_report_route(
    user: Annotated[CurrentUser, Depends(require_user)],
    archive_id: Annotated[str, Path(**_ID_CONSTRAINTS)],
) -> Response:
    limit_export.hit(f"export:{user.id}")
    try:
        async with session_scope() as session:
            archive = await _load_owned_archive(session, archive_id, user)
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
                    .limit(200)
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
                        .limit(200)
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
                owner_email=user.email,
            )
            filename = archive_report_filename(archive)
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(ARCHIVE_UNAVAILABLE) from exc
    except Exception as exc:  # noqa: BLE001 — never leak internals
        logger.error("archive report failed: %s", type(exc).__name__, exc_info=exc)
        raise DependencyUnavailableError(REPORT_UNAVAILABLE) from exc

    return Response(
        content=payload,
        media_type="application/pdf",
        headers={
            **_PRIVATE_HEADERS,
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ---------------------------------------------------------------------------
# Heritage map (curated dataset + approximate route)
# ---------------------------------------------------------------------------

heritage_router = APIRouter(tags=["heritage"])


@heritage_router.get(
    "/heritage/sites",
    response_model=HeritageSitesResponse,
    summary="Curated heritage sites with sourced coordinates",
    description=(
        "The demonstration map layer. Coordinates are published figures marked "
        "approximate; records without a real coordinate are not plotted."
    ),
)
async def heritage_sites_route(
    q: Annotated[
        Optional[str], Query(max_length=120, description="Name, alias, city or state.")
    ] = None,
    state: Annotated[Optional[str], Query(max_length=80)] = None,
    region: Annotated[Optional[str], Query(max_length=120)] = None,
    category: Annotated[Optional[str], Query(max_length=64)] = None,
    period_group: Annotated[
        Optional[str], Query(alias="periodGroup", max_length=32)
    ] = None,
    unesco: Annotated[Optional[bool], Query()] = None,
    story_category: Annotated[
        Optional[str], Query(alias="story", max_length=32)
    ] = None,
    sort: Annotated[
        str, Query(pattern="^(name|state|category|period)$")
    ] = "name",
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HeritageSitesResponse:
    try:
        async with session_scope() as session:
            sites, total = await list_sites(
                session,
                filters=HeritageFilters(
                    q=q,
                    state=state,
                    region=region,
                    category=category,
                    period_group=period_group,
                    unesco=unesco,
                    story_category=story_category,
                    sort=sort,
                ),
                limit=limit,
                offset=offset,
            )
            story_counts = await story_counts_by_site(session)
            story_categories = await story_categories_by_site(session)
            featured = await featured_stories_by_site(session, [site.id for site in sites])
            facets = await heritage_facets(session)
            summary = await heritage_summary(session)
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(
            "The heritage map is unavailable: the database could not be reached."
        ) from exc
    return HeritageSitesResponse(
        sites=[
            HeritageSiteItem(**site)
            for site in sites_payload(
                sites,
                story_counts=story_counts,
                featured=featured,
                story_categories=story_categories,
            )
        ],
        total=total,
        limit=limit,
        offset=offset,
        dataset=DATASET_LABEL,
        notice=(
            "Curated demonstration dataset covering India. Coordinates are "
            "published figures rounded to four decimal places, not a field "
            "survey, and every record links to its source. These are not "
            "user-contributed records and none has been field-verified."
        ),
        summary=summary,
        facets=facets,
    )


@heritage_router.post(
    "/heritage/route",
    response_model=HeritageRouteResponse,
    summary="Build an approximate route between heritage sites",
    description=(
        "Orders the selected sites by straight-line distance. This is not a "
        "road route and contains no travel times, opening hours or prices."
    ),
)
async def heritage_route_route(payload: HeritageRouteRequest) -> HeritageRouteResponse:
    try:
        async with session_scope() as session:
            result = await build_route(
                session,
                site_ids=payload.site_ids,
                start_site_id=payload.start_site_id,
                order=payload.order,
            )
    except ValueError as exc:
        raise UnprocessableEntityError(str(exc)) from exc
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(
            "The heritage map is unavailable: the database could not be reached."
        ) from exc
    return HeritageRouteResponse(**result)


# ---------------------------------------------------------------------------
# Stories & Legends (sourced narratives for curated sites)
# ---------------------------------------------------------------------------

STORIES_UNAVAILABLE = (
    "The story library is unavailable: the database could not be reached."
)


@heritage_router.get(
    "/heritage/sites/{site_id}/stories",
    response_model=SiteStoriesResponse,
    summary="Published stories for one heritage site",
    description=(
        "Returns the site's published stories with their category, epistemic "
        "label, language and provenance counts. Traditions are separate "
        "records; nothing is merged or generated."
    ),
)
async def heritage_site_stories_route(
    site_id: Annotated[str, Path(min_length=1, max_length=64)],
    category: Annotated[Optional[str], Query(max_length=32)] = None,
    language: Annotated[Optional[str], Query(max_length=8)] = None,
) -> SiteStoriesResponse:
    try:
        async with session_scope() as session:
            site, stories, facets = await site_stories(
                session,
                site_id,
                filters=StoryFilters(category=category, language=language),
            )
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(STORIES_UNAVAILABLE) from exc
    if site is None:
        raise NotFoundError("No heritage site exists for this id.")
    return SiteStoriesResponse(
        siteId=site.id,
        siteName=site.name,
        total=len(stories),
        stories=[StorySummary(**story) for story in stories],
        categories=facets,
    )


@heritage_router.post(
    "/heritage/stories/ask",
    response_model=StoryAskResponse,
    summary="Ask a question about one place's stored stories",
    description=(
        "Retrieval only: the answer is assembled from matching stored stories "
        "and their sources. If nothing matches, the response says so instead "
        "of composing an unsupported narrative."
    ),
)
async def heritage_story_ask_route(payload: StoryAskRequest) -> StoryAskResponse:
    try:
        async with session_scope() as session:
            result = await ask_stories(session, payload.site_id, payload.question)
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(STORIES_UNAVAILABLE) from exc
    return StoryAskResponse(**result)


@heritage_router.get(
    "/heritage/stories/{story_id}",
    response_model=StoryDetail,
    summary="Read one published story with sources and versions",
)
async def heritage_story_detail_route(
    story_id: Annotated[str, Path(min_length=1, max_length=96)],
) -> StoryDetail:
    try:
        async with session_scope() as session:
            story = await story_detail(session, story_id)
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(STORIES_UNAVAILABLE) from exc
    if story is None:
        raise NotFoundError("No published story exists for this id.")
    return StoryDetail(**story)


@heritage_router.post(
    "/heritage/sites/{site_id}/stories",
    status_code=202,
    response_model=StorySubmissionResponse,
    summary="Submit a community oral history (starts pending review)",
    responses={
        401: error_response("Sign in to contribute a story."),
        422: error_response("The submission is invalid or consent is missing."),
    },
)
async def heritage_story_submit_route(
    request: Request,
    site_id: Annotated[str, Path(min_length=1, max_length=64)],
    payload: StorySubmissionRequest,
    user: Annotated[CurrentUser, Depends(require_user)],
) -> StorySubmissionResponse:
    verify_same_origin(request)
    if not payload.consent:
        raise UnprocessableEntityError(
            "A contribution can only be stored with the contributor's consent."
        )
    try:
        async with session_scope() as session:
            story = await submit_story(
                session,
                site_id=site_id,
                title=payload.title,
                narrative=payload.narrative,
                language=payload.language,
                community=payload.community,
                tradition=payload.tradition,
                source_note=payload.source_note,
                interview_date=payload.interview_date,
                consent=payload.consent,
                contributor_id=user.id,
                contributor_display=user.display_name,
            )
    except ValueError as exc:
        raise UnprocessableEntityError(str(exc)) from exc
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(STORIES_UNAVAILABLE) from exc
    if story is None:
        raise NotFoundError("No heritage site exists for this id.")
    return StorySubmissionResponse(
        story=StorySummary(**story),
        status="pending",
        message=(
            "Thank you. The contribution is stored as pending and is not "
            "public until a moderator reviews it. Approval records it as a "
            "community account, not as verified history."
        ),
    )


@heritage_router.patch(
    "/heritage/stories/{story_id}/moderation",
    response_model=StorySubmissionResponse,
    summary="Approve or reject a pending community story",
    responses={
        401: error_response("Sign in to moderate contributions."),
        403: error_response("This account is not a story moderator."),
        404: error_response("No pending community story exists for this id."),
        409: error_response("This contribution has already been reviewed."),
    },
)
async def heritage_story_moderation_route(
    request: Request,
    story_id: Annotated[str, Path(min_length=1, max_length=96)],
    payload: StoryModerationRequest,
    user: Annotated[CurrentUser, Depends(require_user)],
) -> StorySubmissionResponse:
    verify_same_origin(request)
    allowed = moderator_emails()
    if not allowed or user.email.strip().lower() not in allowed:
        raise ForbiddenError(
            "Story moderation is restricted to the deployment's moderator allowlist."
        )
    try:
        async with session_scope() as session:
            story, problem = await moderate_story(session, story_id, payload.decision)
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(STORIES_UNAVAILABLE) from exc
    if problem == "not_found":
        raise NotFoundError("No community story exists for this id.")
    if problem == "not_pending":
        raise ConflictApiError("This contribution has already been reviewed.")
    assert story is not None
    return StorySubmissionResponse(
        story=StorySummary(**story),
        status=("published" if payload.decision == "approve" else "rejected"),
        message=(
            "Published as a community account. This records a tradition held "
            "by its contributor; it is not a verified historical claim."
            if payload.decision == "approve"
            else "Rejected. The record is retained for review and is not public."
        ),
    )
