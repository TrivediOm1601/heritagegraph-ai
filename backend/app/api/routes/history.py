"""B7 — personal analysis history and PDF export routes.

Every handler is authenticated and every query is owner-scoped in SQL
(``HistoryEntry.user_id == session user``). Two consequences matter:

* a guessed id returns ``404`` — identical to a nonexistent one, so the
  endpoint cannot be used to probe for other users' records;
* the PDF export selects rows with the same filter, so it can never
  include another account's analyses, whatever ids the client sends.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Path, Query, Response

from ...core.config import ConfigurationError
from ...core.errors import (
    ApiError,
    DependencyUnavailableError,
    NotFoundError,
    UnprocessableEntityError,
    error_response,
)
from ...core.logging import get_logger
from ...core.security import CurrentUser, limit_export, require_user
from ...db.session import RepositoryError, session_scope
from ...repositories.analysis_repository import get_analysis_run
from ...repositories.history_repository import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    count_entries,
    get_entry,
    list_entries,
    list_for_export,
)
from ...schemas.history import HistoryDetailResponse, HistoryItem, HistoryListResponse
from ...services.analysis_service import build_analysis_response
from ...services.export_service import document_from_entries
from ...services.history_service import build_detail, to_item
from ...services.pdf_service import render_pdf, safe_filename

router = APIRouter(tags=["history"])

logger = get_logger("history.routes")

HISTORY_UNAVAILABLE = (
    "History is unavailable: the database could not be reached. Please try again."
)
EXPORT_UNAVAILABLE = (
    "The export could not be generated. Please try again; if it keeps failing, "
    "export a single analysis instead."
)
EMPTY_EXPORT = "There is nothing to export yet: this account has no saved analyses."

_ID_CONSTRAINTS: dict[str, object] = {
    "min_length": 1,
    "max_length": 128,
    "pattern": r"^[^\s]+$",
}

#: Downloads are private per-user documents: never cache them anywhere.
_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


def _pdf_response(payload: bytes, filename: str) -> Response:
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={
            **_PRIVATE_HEADERS,
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get(
    "/history",
    response_model=HistoryListResponse,
    summary="List this account's analysis history",
    description=(
        "Newest first, paginated. Returns only the authenticated user's own "
        "text searches and image analyses."
    ),
    responses={
        401: error_response("Sign in to read your history."),
        422: error_response("The pagination parameters are invalid."),
        503: error_response("The history database is unavailable."),
    },
)
async def list_history(
    user: Annotated[CurrentUser, Depends(require_user)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    kind: Annotated[Optional[str], Query(pattern="^(text_search|image_analysis)$")] = None,
) -> HistoryListResponse:
    try:
        async with session_scope() as session:
            rows = await list_entries(
                session, user_id=user.id, limit=limit, offset=offset, kind=kind
            )
            total = await count_entries(session, user_id=user.id, kind=kind)
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(HISTORY_UNAVAILABLE) from exc

    return HistoryListResponse(
        items=[to_item(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/history/export/pdf",
    summary="Export the whole history as a PDF",
    description=(
        "Generates one PDF containing every (or the selected) record of the "
        "authenticated user. Ownership is enforced in the query, so other "
        "accounts' data can never be included."
    ),
    responses={
        401: error_response("Sign in to export."),
        422: error_response("The requested selection is invalid."),
        429: error_response("Too many exports; please wait before trying again."),
        503: error_response("The export could not be generated."),
    },
)
async def export_history(
    user: Annotated[CurrentUser, Depends(require_user)],
    ids: Annotated[
        Optional[str],
        Query(
            max_length=2000,
            description="Optional comma-separated history ids to export (default: all).",
        ),
    ] = None,
) -> Response:
    limit_export.hit(f"export:{user.id}")

    requested: Optional[list[str]] = None
    if ids:
        requested = [part.strip() for part in ids.split(",") if part.strip()]
        if not requested:
            raise UnprocessableEntityError("No history ids were supplied.")
        if len(requested) > 100:
            raise UnprocessableEntityError("Select at most 100 records to export.")

    try:
        async with session_scope() as session:
            rows = await list_for_export(
                session, user_id=user.id, entry_ids=requested
            )
            if not rows:
                raise NotFoundError(EMPTY_EXPORT)
            document = await document_from_entries(
                session, rows=rows, user_email=user.email
            )
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(EXPORT_UNAVAILABLE) from exc

    try:
        payload = render_pdf(document)
    except ValueError as exc:
        raise NotFoundError(EMPTY_EXPORT) from exc
    except Exception as exc:  # noqa: BLE001 — never leak internals
        logger.error("history PDF export failed: %s", type(exc).__name__, exc_info=exc)
        raise DependencyUnavailableError(EXPORT_UNAVAILABLE) from exc

    logger.info("history export generated (%d records, %d bytes)", len(rows), len(payload))
    return _pdf_response(payload, safe_filename(kind="history"))


@router.get(
    "/history/{entry_id}",
    response_model=HistoryDetailResponse,
    summary="Reopen one history record",
    description=(
        "Returns the stored result exactly as it was produced — reopening "
        "never re-runs the (paid) AI request. Another user's id is a 404."
    ),
    responses={
        401: error_response("Sign in to read your history."),
        404: error_response("No history record exists with that id."),
        503: error_response("The history database is unavailable."),
    },
)
async def history_detail(
    user: Annotated[CurrentUser, Depends(require_user)],
    entry_id: Annotated[str, Path(description="History id from GET /api/history.", **_ID_CONSTRAINTS)],
) -> HistoryDetailResponse:
    try:
        async with session_scope() as session:
            row = await get_entry(session, entry_id, user_id=user.id)
            if row is None:
                raise NotFoundError("No history record exists with that id.")

            analysis_response = None
            if row.kind == "image_analysis" and row.analysis_id:
                run = await get_analysis_run(session, row.analysis_id)
                if run is not None and run.user_id == user.id:
                    analysis_response = await build_analysis_response(session, run)

            return await build_detail(
                session, entry=row, user_id=user.id, analysis_response=analysis_response
            )
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(HISTORY_UNAVAILABLE) from exc


@router.get(
    "/history/{entry_id}/pdf",
    summary="Export one history record as a PDF",
    description=(
        "Server-generated PDF of a single stored analysis or search, "
        "including its sources and an outline of its knowledge graph."
    ),
    responses={
        401: error_response("Sign in to export."),
        404: error_response("No history record exists with that id."),
        429: error_response("Too many exports; please wait before trying again."),
        503: error_response("The export could not be generated."),
    },
)
async def export_entry(
    user: Annotated[CurrentUser, Depends(require_user)],
    entry_id: Annotated[str, Path(description="History id from GET /api/history.", **_ID_CONSTRAINTS)],
) -> Response:
    limit_export.hit(f"export:{user.id}")

    try:
        async with session_scope() as session:
            row = await get_entry(session, entry_id, user_id=user.id)
            if row is None:
                raise NotFoundError("No history record exists with that id.")
            document = await document_from_entries(
                session, rows=[row], user_email=user.email
            )
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(EXPORT_UNAVAILABLE) from exc

    try:
        payload = render_pdf(document)
    except ValueError as exc:
        raise NotFoundError("That record has no exportable content.") from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("entry PDF export failed: %s", type(exc).__name__, exc_info=exc)
        raise DependencyUnavailableError(EXPORT_UNAVAILABLE) from exc

    return _pdf_response(payload, safe_filename(kind="analysis", created_at=row.created_at))
