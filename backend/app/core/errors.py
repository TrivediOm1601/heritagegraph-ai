"""B2/B5 — the canonical API error contract.

Agreed error format (audit §3)::

    {"error": {"code": "ERROR_CODE",
               "message": "Safe explanation of the problem",
               "request_id": "request-uuid"}}

Every non-2xx response produced by this backend has exactly this shape.
``request_id`` is the id generated for the request by the middleware in
``app.main`` (also returned as the ``X-Request-Id`` header), so a user-
facing message can be correlated with the server log line. ``details``
(optionally present inside ``error`` for 422s) carries per-field
validation problems.

Compatibility note: Module A's current parser (``readErrorDetail`` in
``frontend/src/services/api.ts``) reads ``error``/``message`` as *strings*;
with the agreed nested format it falls back to showing the raw JSON text.
The functional contract (error vs success, status codes, codes) is
unchanged — see INTEGRATION_ISSUES.md INT-007 for the one-line Module A
parser update that restores pretty messages.

Handlers are installed once per app in ``app.main.create_app`` via
:func:`install_error_handlers`, covering:

* :class:`ApiError` subclasses       -> their own status code
* pydantic/FastAPI validation errors -> 422 ``validation_error``
* Starlette ``HTTPException``        -> 404/405/... with the same envelope
* unhandled exceptions               -> 500 ``internal_error`` (message
  never includes internals such as tracebacks or connection strings; the
  exception itself is logged server-side with its ``request_id``)
"""

from __future__ import annotations

from typing import Any, Iterable, Literal, Optional

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException

from .logging import get_logger

# Machine-readable error codes. Clients may branch on these; they are part
# of the contract and must not be renamed casually.
ErrorCode = Literal[
    "bad_request",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "method_not_allowed",
    "validation_error",
    "payload_too_large",
    "unsupported_media_type",
    "rate_limited",
    "dependency_unavailable",
    "internal_error",
]

INTERNAL_ERROR_MESSAGE = "The server hit an unexpected error. Please try again."

logger = get_logger("errors")


class ErrorDetail(BaseModel):
    """One field-level validation problem (inside ``error.details``)."""

    loc: list[str] = Field(default_factory=list, description="Location of the offending value (e.g. body.file).")
    msg: str = Field(description="What is wrong with the value.")
    type: str = Field(description="Machine-readable error type.")


class ErrorObject(BaseModel):
    """The object carried under ``error``."""

    code: str = Field(description="Machine-readable error code; clients may branch on it.")
    message: str = Field(description="Human-readable, safe message; the UI displays this.")
    request_id: Optional[str] = Field(
        default=None, description="Id of the request that failed (also the X-Request-Id header)."
    )
    details: Optional[list[ErrorDetail]] = Field(
        default=None, description="Per-field validation problems, when applicable (422)."
    )


class ErrorBody(BaseModel):
    """The canonical error envelope documented in OpenAPI."""

    error: ErrorObject


def error_response(description: str) -> dict[str, Any]:
    """OpenAPI ``responses`` entry for an error status with the canonical body."""
    return {"description": description, "model": ErrorBody}


def error_payload(
    message: str,
    code: str,
    details: Optional[Iterable[dict[str, Any]]] = None,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build the agreed error body ``{"error": {code, message, request_id?}}``."""
    error: dict[str, Any] = {"code": code, "message": message}
    if request_id:
        error["request_id"] = request_id
    if details:
        error["details"] = list(details)
    return {"error": error}


def request_id_of(request: Any) -> Optional[str]:
    """The id attached by the request-id middleware, when available."""
    state = getattr(request, "state", None)
    return getattr(state, "request_id", None)


class ApiError(Exception):
    """Base class for errors that map directly onto the API contract."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: Optional[list[dict[str, Any]]] = None,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code

    def to_payload(self, request_id: Optional[str] = None) -> dict[str, Any]:
        return error_payload(self.message, self.code, self.details, request_id)


class BadRequestError(ApiError):
    status_code = 400
    code = "bad_request"


class UnauthorizedError(ApiError):
    """No valid session: the caller must sign in."""

    status_code = 401
    code = "unauthorized"


class ForbiddenError(ApiError):
    """Authenticated, but not allowed (ownership or CSRF check failed)."""

    status_code = 403
    code = "forbidden"


class NotFoundError(ApiError):
    status_code = 404
    code = "not_found"


class ConflictApiError(ApiError):
    """The request conflicts with existing state (duplicate account, ...)."""

    status_code = 409
    code = "conflict"


class RateLimitedError(ApiError):
    """Too many attempts on a sensitive endpoint."""

    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PayloadTooLargeError(ApiError):
    status_code = 413
    code = "payload_too_large"


class UnsupportedMediaTypeError(ApiError):
    status_code = 415
    code = "unsupported_media_type"


class UnprocessableEntityError(ApiError):
    status_code = 422
    code = "validation_error"


class DependencyUnavailableError(ApiError):
    """A required backend dependency (database, OCR/vision, service) is
    not wired up yet. Distinct from a validation failure so clients never
    mistake "cannot process right now" for "your request was rejected"."""

    status_code = 503
    code = "dependency_unavailable"


def _validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for error in exc.errors():
        details.append(
            {
                "loc": [str(part) for part in error.get("loc", ())],
                "msg": str(error.get("msg", "")),
                "type": str(error.get("type", "")),
            }
        )
    return details


def install_error_handlers(app: FastAPI) -> None:
    """Register the canonical error handlers on the application."""

    @app.exception_handler(ApiError)
    async def _api_error(request, exc: ApiError):
        headers = None
        retry_after = getattr(exc, "retry_after", None)
        if isinstance(retry_after, int) and retry_after > 0:
            headers = {"Retry-After": str(retry_after)}
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_payload(request_id_of(request)),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=error_payload(
                "Request validation failed. Check the highlighted fields.",
                "validation_error",
                _validation_details(exc),
                request_id_of(request),
            ),
        )

    @app.exception_handler(HTTPException)
    async def _http_error(request, exc: HTTPException):
        # Starlette raises these for unknown routes/methods; keep them in
        # the same envelope instead of FastAPI's {"detail": ...} body.
        detail = exc.detail
        if isinstance(detail, str) and detail.strip():
            message = detail
        elif exc.status_code == 404:
            message = "The requested endpoint was not found."
        elif exc.status_code == 405:
            message = "That method is not allowed for this endpoint."
        else:
            message = f"Request failed (HTTP {exc.status_code})."
        code = (
            "not_found"
            if exc.status_code == 404
            else "method_not_allowed"
            if exc.status_code == 405
            else "http_error"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(message, str(code), request_id=request_id_of(request)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request, exc: Exception):
        # Deliberately generic: internals (paths, DSNs, stack traces) must
        # never reach a client. The full traceback goes to the server log
        # under the same request_id for correlation.
        request_id = request_id_of(request)
        logger.error(
            "unhandled exception (request_id=%s): %s", request_id, type(exc).__name__, exc_info=exc
        )
        response = JSONResponse(
            status_code=500,
            content=error_payload(INTERNAL_ERROR_MESSAGE, "internal_error", request_id=request_id),
        )
        if request_id:
            response.headers["X-Request-Id"] = request_id
        return response
