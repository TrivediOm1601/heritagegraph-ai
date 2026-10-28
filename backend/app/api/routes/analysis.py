"""B2/B3 — analysis submission and retrieval routes.

Thin handlers around the B3 orchestrator
(:mod:`app.services.analysis_service`):

* **POST /api/analysis** — validates the upload against the shared
  contract (type/extension, size, emptiness), runs D1's real content
  gate (decode/dimension limits, in a worker thread so the event loop
  never blocks), refuses to queue when live processing is not
  configured (actionable 503 — unless ``HG_DEMO_MODE`` is on), records
  a ``queued`` run row, and hands the work to a background task that
  returns ``202`` immediately with the analysis id.
* **GET /api/analysis/{analysis_id}** — serves the persisted lifecycle
  state: ``queued``/``processing`` while the background task runs,
  ``completed`` with extracted text, entities and matches, or ``failed``
  with a safe error message. Unknown id → ``404``; database unavailable
  → canonical ``503`` — a broken dependency is never rendered as a
  successful analysis.

No response is fabricated: every field comes from Module C's storage
or from the run's own processing outcome.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Path, Request, UploadFile

from ...core.config import ConfigurationError
from ...core.errors import (
    ApiError,
    DependencyUnavailableError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    UnprocessableEntityError,
    error_response,
)
from ...core.logging import get_logger
from ...core.security import (
    CurrentUser,
    limit_analysis,
    require_user,
    verify_same_origin,
)
from ...db.session import RepositoryError, session_scope
from ...repositories.analysis_repository import create_analysis_run, get_analysis_run
from ...repositories.history_repository import create_entry
from ...schemas.analysis import (
    ACCEPTED_IMAGE_EXTENSIONS,
    ACCEPTED_IMAGE_TYPES,
    ANALYSIS_FILE_FIELD,
    GENERIC_MEDIA_TYPE,
    MAX_UPLOAD_BYTES,
    AnalysisResponse,
    SubmitAnalysisResponse,
)
from ...services.analysis_service import (
    SUBMISSION_UNAVAILABLE,
    build_analysis_response,
    processing_configuration_error,
    run_analysis,
)
from ...services.image_service import prepare_image

router = APIRouter(tags=["analysis"])

logger = get_logger("analysis.routes")

#: Canonical failure text when results cannot be read from storage.
RESULTS_UNAVAILABLE = (
    "Analysis results are unavailable: the database could not be reached."
)

_ID_CONSTRAINTS: dict[str, object] = {
    "min_length": 1,
    "max_length": 128,
    "pattern": r"^[^\s]+$",
}


def _format_limit(max_bytes: int) -> str:
    if max_bytes >= 1024 * 1024 and max_bytes % (1024 * 1024) == 0:
        return f"{max_bytes // (1024 * 1024)} MB"
    if max_bytes >= 1024 and max_bytes % 1024 == 0:
        return f"{max_bytes // 1024} KB"
    return f"{max_bytes} bytes"


def validate_image_upload(file: UploadFile) -> None:
    """Type/extension guard mirroring ``validateImageFile`` in the frontend."""
    filename = file.filename or ""
    dot = filename.rfind(".")
    extension = filename[dot:].lower() if dot > 0 else ""
    content_type = (file.content_type or "").split(";")[0].strip().lower()

    if content_type:
        acceptable = content_type in ACCEPTED_IMAGE_TYPES or (
            content_type == GENERIC_MEDIA_TYPE and extension in ACCEPTED_IMAGE_EXTENSIONS
        )
    else:
        acceptable = extension in ACCEPTED_IMAGE_EXTENSIONS

    if not acceptable:
        raise UnsupportedMediaTypeError(
            f'"{filename or "upload"}" is not a supported image. '
            "Use JPEG, PNG, WebP, GIF, TIFF or AVIF."
        )


async def read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Stream the upload while enforcing the size limit; return its bytes."""
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise PayloadTooLargeError(
                f"The uploaded image exceeds the {_format_limit(max_bytes)} limit."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _owned_run(run, user: CurrentUser):
    """Return ``run`` only when ``user`` owns it, else a canonical 404.

    Another user's run and a nonexistent id are indistinguishable, so a
    guessed id never confirms that an analysis exists.
    """
    if run is None or run.user_id != user.id:
        raise NotFoundError("No analysis exists with that id.")
    return run


async def _run_in_background(
    data: bytes,
    prepared: "object",
    analysis_id: str,
    filename: str,
    content_type: str,
    user_id: str,
) -> None:
    """Background entry point: every failure is recorded by run_analysis;
    this guard only keeps an unexpected crash visible in the server log."""
    try:
        await run_analysis(
            data,
            filename,
            content_type,
            analysis_id=analysis_id,
            prepared=prepared,  # type: ignore[arg-type]
            user_id=user_id,
        )
    except Exception:  # noqa: BLE001 — background boundary
        logger.exception("background analysis %s crashed", analysis_id)


@router.post(
    "/analysis",
    response_model=SubmitAnalysisResponse,
    status_code=202,
    summary="Submit an image for analysis",
    description=(
        "Accepts a multipart image upload under the field name "
        f"`{ANALYSIS_FILE_FIELD}`, validates it (type, size, real decode) "
        "and queues it for analysis, returning the id to poll via "
        "GET `/api/analysis/{analysisId}`. Processing runs in the "
        "background; the run's status transitions queued → processing → "
        "completed/failed. Without configured OCR/vision providers (and "
        "without HG_DEMO_MODE) the request is refused with 503 instead "
        "of queuing work that could not run."
    ),
    responses={
        202: {"description": "The image was accepted and queued.", "model": SubmitAnalysisResponse},
        401: error_response("Sign in to submit an analysis."),
        413: error_response("The image exceeds the upload size limit or decode limits."),
        429: error_response("Too many uploads; please wait before trying again."),
        415: error_response("The upload is not a supported image type."),
        422: error_response(
            f"The multipart request is missing or invalid (no `{ANALYSIS_FILE_FIELD}` "
            "field, an empty file, or undecodable image content)."
        ),
        503: error_response(
            "Analysis processing is not configured, or the database is unavailable."
        ),
    },
)
async def submit_analysis(
    background: BackgroundTasks,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
    file: Annotated[
        UploadFile,
        File(
            description=(
                f"Image to analyse (JPEG, PNG, WebP, GIF, TIFF or AVIF); "
                f"multipart field `{ANALYSIS_FILE_FIELD}`, max "
                f"{_format_limit(MAX_UPLOAD_BYTES)}."
            )
        ),
    ],
) -> SubmitAnalysisResponse:
    # Cookie-authenticated mutation: reject a cross-origin write outright.
    verify_same_origin(request)
    # Each upload spends provider credits — bound them per account.
    limit_analysis.hit(f"upload:{user.id}")

    validate_image_upload(file)
    data = await read_upload_limited(file, MAX_UPLOAD_BYTES)
    if not data:
        raise UnprocessableEntityError("The uploaded file is empty.")

    filename = file.filename or ""
    content_type = (file.content_type or "").split(";")[0].strip().lower()

    # D1's content gate: decode + dimension limits, CPU-bound → worker
    # thread so a heavy image cannot block the event loop. Canonical
    # 413/415/422 errors propagate to the shared handlers.
    prepared = await asyncio.to_thread(
        prepare_image, data, filename=filename, content_type=content_type
    )

    # Fail fast when live processing has no credentials and fixtures are
    # not explicitly enabled: queueing would only produce a failed run.
    config_problem = processing_configuration_error()
    if config_problem:
        raise DependencyUnavailableError(config_problem)

    analysis_id = str(uuid.uuid4())
    try:
        async with session_scope() as session:
            await create_analysis_run(
                session,
                analysis_id=analysis_id,
                original_filename=filename[:255] or None,
                content_type=content_type[:100] or None,
                user_id=user.id,
            )
            # The history row is created here, once, at submission — not by
            # the frontend and not on every status poll (deduplication by
            # construction). The run updates its status as processing ends.
            await create_entry(
                session,
                user_id=user.id,
                kind="image_analysis",
                query=(filename[:500] or None),
                analysis_id=analysis_id,
                status="queued",
                summary="Image analysis queued.",
            )
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(SUBMISSION_UNAVAILABLE) from exc

    background.add_task(
        _run_in_background, data, prepared, analysis_id, filename, content_type, user.id
    )
    logger.info("analysis %s queued (%d bytes, %s)", analysis_id, len(data), content_type)
    return SubmitAnalysisResponse(analysis_id=analysis_id, status="queued")


@router.get(
    "/analysis/{analysis_id}",
    response_model=AnalysisResponse,
    summary="Get an analysis",
    description=(
        "Returns the lifecycle state, extraction output, detected entities, "
        "matched historical records, warnings and (when failed) the error "
        "of one analysis. Poll this endpoint after a 202 submission until "
        "`status` is `completed` or `failed`."
    ),
    responses={
        401: error_response("Sign in to read an analysis."),
        404: error_response("No analysis exists with that id."),
        422: error_response("The analysis id is missing or malformed."),
        503: error_response("The database serving analysis results is unavailable."),
    },
)
async def get_analysis(
    user: Annotated[CurrentUser, Depends(require_user)],
    analysis_id: Annotated[str, Path(description="Analysis id returned by POST /api/analysis.", **_ID_CONSTRAINTS)],
) -> AnalysisResponse:
    try:
        async with session_scope() as session:
            run = await get_analysis_run(session, analysis_id)
            # Ownership is enforced here, on the server, from the session.
            _owned_run(run, user)
            return await build_analysis_response(session, run)
    except ApiError:
        raise
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(RESULTS_UNAVAILABLE) from exc
