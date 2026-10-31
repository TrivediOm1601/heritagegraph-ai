"""D2 — OCR adapter (three selectable providers behind one call).

``run_ocr`` is the single entry point the analysis pipeline uses; which
provider answers is decided by ``OCR_PROVIDER``:

=========================  =========================================
``ocr_space``              OCR.space's documented HTTP API
                           (``POST https://api.ocr.space/parse/image``,
                           API key in the ``apikey`` request header).
                           Recommended: free key, no account approval.
``google_vision``          Google Cloud Vision ``files:annotate``.
``http`` (default)         A generic multipart endpoint you fully
                           configure yourself.
=========================  =========================================

OCR.space contract implemented here (from https://ocr.space/ocrapi):

* request: ``POST`` multipart with the image under the field name
  ``file`` (filename preserved), ``language`` (default ``auto``),
  ``OCREngine`` (default ``2``, the documented general-purpose engine)
  and ``scale`` for small inscriptions; the key travels in the
  ``apikey`` **request header**;
* response: JSON whose ``IsErroredOnProcessing``/``ErrorMessage`` carry
  provider failures *inside* an HTTP 200 — those are classified into the
  auth/rate-limit/timeout hierarchy instead of being read as text;
  ``ParsedResults[*].ParsedText`` is the recognised text. An empty text
  with ``OCRExitCode: 1`` is a successful "no text in this image"
  answer, never a failure.

Size/format: the free plan accepts 1 MB per file and PNG/JPG/GIF/TIF/BMP.
Payloads over that bound (or in another format, e.g. WebP) are
recompressed — first quality, then only as much resolution as needed —
and given up on with an actionable message rather than sent anyway.

Generic endpoint contract for ``OCR_API_URL`` (documented here because
no vendor API is fixed for that mode):

* request: ``POST`` ``multipart/form-data`` with the image uploaded
  under the field name ``image`` (original filename and content type
  preserved), plus ``Authorization: Bearer <OCR_API_KEY>``;
* response: either JSON ``{"text": "..."}`` (the key may also be nested
  under ``result``/``data``) or a ``text/plain`` body carrying the
  recognised text directly.

Failure behaviour (D2 acceptance criteria):

* timeouts, connection failures, 401/403, 429 (with ``Retry-After``)
  and 5xx are classified into the shared :mod:`app.integrations`
  exception hierarchy — safe messages only, no URL/key/body leakage;
* a 200 with an unusable payload raises :class:`ProviderResponseError`
  instead of being silently treated as "no text found";
* missing credentials fail *before* any request is made, naming exactly
  which variables to set.

Real calls are distinguishable from fixtures: the adapter builds its own
network client only when the caller does not inject one, and the result
carries ``provenance="live"`` (adapter-owned network client) or
``"injected-client"`` (tests passing an ``httpx.MockTransport`` client).
"""

from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass, field
from typing import Mapping

import httpx
from PIL import Image

from . import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    classify_http_error,
    read_timeout,
    require_env,
    validate_http_url,
)
from .google_vision import credentials_configured as google_credentials_configured
from .google_vision import run_google_vision

#: Provider label used in exception messages and logs (never a URL).
PROVIDER = "OCR service"

#: Environment variables this adapter reads.
ENV_PROVIDER = "OCR_PROVIDER"
ENV_URL = "OCR_API_URL"
ENV_KEY = "OCR_API_KEY"
ENV_TIMEOUT = "OCR_TIMEOUT"

#: OCR.space variables (see :func:`configuration_problem` — the key is
#: the only one that must be set; the others have documented defaults).
ENV_SPACE_KEY = "OCR_SPACE_API_KEY"
ENV_SPACE_URL = "OCR_SPACE_API_URL"
ENV_SPACE_LANGUAGE = "OCR_SPACE_LANGUAGE"
ENV_SPACE_ENGINE = "OCR_SPACE_ENGINE"
ENV_SPACE_MAX_BYTES = "OCR_SPACE_MAX_BYTES"

#: Documented endpoint and defaults from https://ocr.space/ocrapi.
DEFAULT_SPACE_URL = "https://api.ocr.space/parse/image"
#: ``auto`` is the documented default for engines 2 and 3: it does not
#: assume English, which a historical inscription usually is not.
DEFAULT_SPACE_LANGUAGE = "auto"
#: Engine 2 is the documented default and the general-purpose choice.
DEFAULT_SPACE_ENGINE = "2"
#: Free-plan file limit ("1 MB" in the plan table). Deliberately the
#: conservative byte count, and overridable for a paid plan.
DEFAULT_SPACE_MAX_BYTES = 1_000_000
#: Documented engines; anything else is a typo, not a fallback.
SPACE_ENGINES: tuple[str, ...] = ("1", "2", "3")
#: Formats OCR.space documents for image input (WebP is not one of them).
SPACE_MIME_TYPES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/jpg", "image/bmp", "image/tiff", "image/gif"}
)
SPACE_MIME_EXTENSIONS: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/bmp": ".bmp",
    "image/tiff": ".tif",
    "image/gif": ".gif",
}

#: Supported OCR providers. ``ocr_space`` is OCR.space (recommended);
#: ``google_vision`` uses the official Cloud Vision annotate API (see
#: :mod:`app.integrations.google_vision`); ``http`` is the generic
#: multipart endpoint described below.
PROVIDER_OCR_SPACE = "ocr_space"
PROVIDER_GOOGLE = "google_vision"
PROVIDER_HTTP = "http"
SUPPORTED_PROVIDERS: tuple[str, ...] = (PROVIDER_OCR_SPACE, PROVIDER_GOOGLE, PROVIDER_HTTP)

#: Provider selected when OCR_PROVIDER is unset (backwards compatible).
DEFAULT_PROVIDER = PROVIDER_HTTP

#: Multipart field name the endpoint must read the image from.
IMAGE_FIELD = "image"

#: Accept both JSON and plain text responses.
_ACCEPTED_MEDIA_TYPES = ("application/json", "text/plain")


@dataclass(frozen=True)
class OcrResult:
    """Recognised text plus how it was obtained.

    ``provenance`` is the fixture/live discriminator (see module
    docstring); ``engine`` is the configured endpoint's host so logs can
    say *which* engine answered without printing the full URL.
    """

    text: str
    engine: str
    provenance: str  # "live" | "injected-client"
    #: Which adapter produced this result (``google_vision`` | ``http``).
    provider: str = PROVIDER_HTTP
    #: Locale hints reported by the provider (metadata only, may be empty).
    languages: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        """True when the image contained no recognisable text."""
        return not self.text.strip()


def selected_provider(environ: Mapping[str, str] | None = None) -> str:
    """The configured OCR provider (validated against the support list)."""
    import os

    env = os.environ if environ is None else environ
    raw = (env.get(ENV_PROVIDER) or "").strip().lower()
    if not raw:
        return DEFAULT_PROVIDER
    if raw not in SUPPORTED_PROVIDERS:
        raise ProviderConfigurationError(
            f"{PROVIDER} is misconfigured: {ENV_PROVIDER} must be one of "
            + ", ".join(SUPPORTED_PROVIDERS)
            + f" (got {raw!r}).",
            provider=PROVIDER,
        )
    return raw


def configuration_problem(
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """``None`` when OCR can run, otherwise an actionable message.

    Used by the analysis configuration gate and diagnostics so a missing
    provider is reported with the *right* variable names for the selected
    provider: ``OCR_SPACE_API_KEY`` for OCR.space,
    ``GOOGLE_APPLICATION_CREDENTIALS``/``GOOGLE_VISION_API_KEY`` for
    Google Cloud Vision — never a variable belonging to a provider that
    is not selected.
    """
    import os

    env = os.environ if environ is None else environ
    try:
        provider = selected_provider(env)
    except ProviderConfigurationError as exc:
        return str(exc)

    if provider == PROVIDER_GOOGLE:
        if google_credentials_configured(env):
            return None
        return (
            "Google Cloud Vision is not configured: set "
            "GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON file, "
            "or set GOOGLE_VISION_API_KEY (OCR_PROVIDER=google_vision)."
        )

    if provider == PROVIDER_OCR_SPACE:
        # The endpoint defaults to the official one, so the key alone is
        # what an operator has to provide.
        if (env.get(ENV_SPACE_KEY) or "").strip():
            return None
        return (
            f"{PROVIDER} is not configured: set {ENV_SPACE_KEY} — a free key "
            "is issued immediately at https://ocr.space/ocrapi/freekey "
            "(keep it out of git and out of the frontend; see .env.example)."
        )

    missing = [
        name for name in (ENV_URL, ENV_KEY) if not (env.get(name) or "").strip()
    ]
    if missing:
        return (
            f"{PROVIDER} is not configured: set "
            + ", ".join(missing)
            + ", or set OCR_PROVIDER=ocr_space with "
            + ENV_SPACE_KEY
            + " to use OCR.space, or OCR_PROVIDER=google_vision for Google "
            "Cloud Vision."
        )
    return None


def _engine_label(url: str) -> str:
    """Host part of the endpoint for logs — never the full URL or query."""
    try:
        return httpx.URL(url).host or "unknown"
    except Exception:  # pragma: no cover - defensive; URL is validated earlier
        return "unknown"


def _extract_text(payload: object) -> str | None:
    """Pull the recognised text out of a decoded JSON body.

    Returns ``None`` when no text key exists at all (malformed), a
    string otherwise — including the empty string, which is a legitimate
    "no text in this image" answer.
    """
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for container in (payload, payload.get("result"), payload.get("data")):
            if isinstance(container, dict):
                value = container.get("text")
                if isinstance(value, str):
                    return value
    return None


# ---------------------------------------------------------------------------
# OCR.space (https://ocr.space/ocrapi) — the recommended provider
# ---------------------------------------------------------------------------


def _read_max_bytes(environ: Mapping[str, str]) -> int:
    """File-size bound for the OCR.space request (free plan: 1 MB)."""
    raw = (environ.get(ENV_SPACE_MAX_BYTES) or "").strip()
    if not raw:
        return DEFAULT_SPACE_MAX_BYTES
    try:
        limit = int(raw)
    except ValueError:
        raise ProviderConfigurationError(
            f"{PROVIDER} is misconfigured: {ENV_SPACE_MAX_BYTES} must be a "
            f"number of bytes (got {raw!r}).",
            provider=PROVIDER,
        ) from None
    if limit < 1024:
        raise ProviderConfigurationError(
            f"{PROVIDER} is misconfigured: {ENV_SPACE_MAX_BYTES} must be at "
            f"least 1024 bytes (got {limit}).",
            provider=PROVIDER,
        )
    return limit


def _payload_filename(filename: str, mime: str) -> str:
    """A base name with the extension matching ``mime``.

    OCR.space picks the parser from the declared type and the filename,
    so an uploaded ``scan.webp`` sent as JPEG must not keep the ``.webp``
    suffix it no longer has.
    """
    import os

    stem = os.path.splitext(os.path.basename(filename or "upload"))[0] or "upload"
    extension = SPACE_MIME_EXTENSIONS.get(mime, ".jpg")
    return f"{stem}{extension}"


def _encode_for_ocr_space(
    image: "Image.Image", *, max_bytes: int, filename: str
) -> tuple[bytes, str, str]:
    """Best-effort fit of a decoded image into the provider's file limit.

    Legibility comes first: quality is reduced before resolution, and
    resolution is only cut while the result stays above a floor that keeps
    small inscriptions readable. If nothing fits, an actionable error is
    raised instead of sending a payload the provider will reject (OCR is
    an optional channel here — the analysis continues vision-only with
    that message as its warning).
    """
    # Longest edge below which we stop shrinking: further reduction is far
    # more likely to destroy the text than to save the request.
    min_edge = 1400

    current = image
    while True:
        for quality in (92, 85, 75, 65):
            buffer = io.BytesIO()
            try:
                current.convert("RGB").save(
                    buffer, format="JPEG", quality=quality, optimize=True
                )
            except OSError as exc:  # pragma: no cover - defensive
                raise ProviderConfigurationError(
                    f"{PROVIDER} could not encode the image for OCR "
                    f"({type(exc).__name__}).",
                    provider=PROVIDER,
                ) from exc
            data = buffer.getvalue()
            if len(data) <= max_bytes:
                return data, "image/jpeg", _payload_filename(filename, "image/jpeg")

        width, height = current.size
        if max(width, height) <= min_edge:
            break
        # Scale by 0.75 in one step instead of repeated 0.8s (fewer rounds).
        scale = max(0.75, min_edge / max(width, height))
        current = current.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.LANCZOS,
        )

    raise ProviderConfigurationError(
        f"{PROVIDER} could not accept the image: it is larger than the "
        f"{max_bytes} byte file limit even after recompression, and shrinking "
        "it further would make the text unreadable. Crop or export the image "
        f"under {max_bytes} bytes ({ENV_SPACE_MAX_BYTES} raises the limit for "
        "a paid plan).",
        provider=PROVIDER,
    )


def _prepare_payload_ocr_space(
    image: bytes,
    *,
    filename: str,
    content_type: str,
    max_bytes: int,
) -> tuple[bytes, str, str]:
    """``(bytes, mime, filename)`` to upload — untouched when possible.

    * a documented format within the file limit is forwarded byte for
      byte, keeping the original crispness;
    * an undocumented format (WebP, AVIF, a lying content type) is
      transcoded to PNG first — lossless, so text keeps its edges;
    * anything over the limit goes through :func:`_encode_for_ocr_space`.

    Raises :class:`ProviderConfigurationError` for an undecodable image
    or one that cannot be made to fit: both stop *before* the request and
    both are rendered as an explicit "OCR skipped" warning upstream,
    which is the honest outcome for an optional channel (the provider
    never saw the file, so this is deliberately not a provider error).
    """
    media = (content_type or "").split(";")[0].strip().lower()
    if media in SPACE_MIME_TYPES and len(image) <= max_bytes:
        return image, media, _payload_filename(filename, media)

    try:
        decoded = Image.open(io.BytesIO(image))
        decoded.load()
    except Exception as exc:  # noqa: BLE001 — any decode failure is the finding
        raise ProviderConfigurationError(
            f"{PROVIDER} could not read the uploaded image "
            f"({type(exc).__name__}); OCR was skipped. Upload a JPEG, PNG, "
            "GIF, TIFF or BMP instead.",
            provider=PROVIDER,
        ) from exc

    # An unsupported-but-decodable format fits as lossless PNG.
    if media not in SPACE_MIME_TYPES:
        buffer = io.BytesIO()
        try:
            decoded.save(buffer, format="PNG", optimize=True)
        except OSError as exc:  # pragma: no cover - defensive
            raise ProviderConfigurationError(
                f"{PROVIDER} could not transcode the image ({type(exc).__name__}).",
                provider=PROVIDER,
            ) from exc
        if len(buffer.getvalue()) <= max_bytes:
            return buffer.getvalue(), "image/png", _payload_filename(filename, "image/png")

    return _encode_for_ocr_space(decoded, max_bytes=max_bytes, filename=filename)


def _classify_space_error(message: str, *, key: str) -> ProviderError:
    """Turn a provider-reported failure into the shared exception type.

    OCR.space reports failures inside HTTP 200 bodies (``ErrorMessage``),
    so the body — not the status — is what says whether credentials were
    rejected, the quota ran out or the image timed out. The message is
    collapsed to one line, capped and stripped of the key itself before
    it can reach a log or the UI.
    """
    safe = " ".join(str(message).split())
    if key:
        safe = safe.replace(key, "[redacted]")
    safe = safe[:300]
    lowered = safe.lower()

    if any(
        marker in lowered
        for marker in (
            "invalid or missing api key",
            "invalid api key",
            "missing api key",
            "api key is required",
            "api key is invalid",
            "unauthorized",
            "forbidden",
        )
    ):
        return ProviderAuthenticationError(
            f"{PROVIDER} rejected the configured API key. Check {ENV_SPACE_KEY}.",
            provider=PROVIDER,
        )
    if any(
        marker in lowered
        for marker in (
            "rate limit",
            "ratelimit",
            "quota",
            "too many",
            "limit exceeded",
            "exceeded the limit",
            "500 requests",
            "requests per day",
            "daily limit",
            "monthly limit",
        )
    ):
        return ProviderRateLimitError(
            f"{PROVIDER} refused the request because the plan's quota is used "
            "up (the free plan allows 500 requests per day per IP). Try again "
            "later or raise the plan.",
            provider=PROVIDER,
        )
    if "timeout" in lowered or "timed out" in lowered or "time out" in lowered:
        return ProviderTimeoutError(
            f"{PROVIDER} timed out while processing the image. Try a smaller "
            "or clearer image.",
            provider=PROVIDER,
        )
    return ProviderError(
        f"{PROVIDER} could not process the image: {safe}",
        provider=PROVIDER,
    )


def _parse_space_response(raw: str, *, key: str) -> str:
    """Extract recognised text from an OCR.space JSON body.

    An HTTP 200 is *not* success: ``IsErroredOnProcessing``/``ErrorMessage``
    carry auth, quota and processing failures inside a 200, and only the
    body distinguishes them. Empty text with a successful exit code is a
    legitimate "this image has no readable text" answer.
    """
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProviderResponseError(
            f"{PROVIDER} returned a response that is not valid JSON.",
            provider=PROVIDER,
        ) from exc
    if not isinstance(payload, dict):
        raise ProviderResponseError(
            f"{PROVIDER} returned a JSON response that is not an object.",
            provider=PROVIDER,
        )

    message = payload.get("ErrorMessage")
    message_text = message.strip() if isinstance(message, str) else ""
    errored = bool(payload.get("IsErroredOnProcessing"))
    if errored:
        raise _classify_space_error(
            message_text or "the provider reported a processing error", key=key
        )

    results = payload.get("ParsedResults")
    if not isinstance(results, list) or not results:
        if message_text:
            raise _classify_space_error(message_text, key=key)
        raise ProviderResponseError(
            f"{PROVIDER} returned no OCR results for the image.",
            provider=PROVIDER,
        )

    texts: list[str] = []
    entry_errors: list[str] = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        text = entry.get("ParsedText")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
        error = entry.get("ErrorMessage")
        if isinstance(error, str) and error.strip():
            entry_errors.append(error.strip())

    recognised = "\n".join(texts).strip()
    if not recognised:
        if entry_errors:
            raise _classify_space_error(entry_errors[0], key=key)
        exit_code = payload.get("OCRExitCode")
        if exit_code in (3, 4):
            # 3 = every page failed to parse, 4 = fatal error: a failure,
            # not "the image contained no text".
            raise ProviderError(
                f"{PROVIDER} could not read any text from the image "
                f"(OCRExitCode {exit_code}).",
                provider=PROVIDER,
            )
    # ``""`` is a successful no-text answer; callers distinguish it via
    # ``OcrResult.is_empty``.
    return recognised


async def _run_ocr_space(
    image: bytes,
    *,
    filename: str,
    content_type: str,
    client: httpx.AsyncClient | None = None,
    environ: Mapping[str, str] | None = None,
) -> OcrResult:
    """Send ``image`` to OCR.space's documented endpoint.

    Configuration is validated *before* any request: a missing key names
    ``OCR_SPACE_API_KEY``, a bad timeout/engine/limit names its own
    variable, and a non-http(s) ``OCR_SPACE_API_URL`` is rejected.

    Raises the shared hierarchy exactly like the other providers:
    timeout/connection/auth/rate-limit/5xx before parsing, and
    :class:`ProviderResponseError` for a 2xx body that cannot be parsed.
    """
    env = os.environ if environ is None else environ
    problem = configuration_problem(env)
    if problem:
        raise ProviderConfigurationError(problem, provider=PROVIDER)

    key = (env.get(ENV_SPACE_KEY) or "").strip()
    url = (env.get(ENV_SPACE_URL) or "").strip() or DEFAULT_SPACE_URL
    validate_http_url(PROVIDER, ENV_SPACE_URL, url)
    timeout = read_timeout(PROVIDER, ENV_TIMEOUT, default=30.0, environ=env)

    language = (env.get(ENV_SPACE_LANGUAGE) or DEFAULT_SPACE_LANGUAGE).strip()
    engine = (env.get(ENV_SPACE_ENGINE) or DEFAULT_SPACE_ENGINE).strip()
    if engine not in SPACE_ENGINES:
        raise ProviderConfigurationError(
            f"{PROVIDER} is misconfigured: {ENV_SPACE_ENGINE} must be one of "
            + ", ".join(SPACE_ENGINES)
            + f" (got {engine!r}).",
            provider=PROVIDER,
        )
    max_bytes = _read_max_bytes(env)

    payload, payload_type, payload_name = _prepare_payload_ocr_space(
        image,
        filename=filename,
        content_type=content_type,
        max_bytes=max_bytes,
    )

    request = httpx.Request(
        "POST",
        url,
        headers={"apikey": key, "Accept": "application/json"},
        # Documented fields only: file, language, OCREngine, scale,
        # isOverlayRequired (bounding boxes would be pure overhead here).
        data={
            "language": language,
            "OCREngine": engine,
            "scale": "true",
            "isOverlayRequired": "false",
        },
        files={"file": (payload_name, payload, payload_type)},
    )
    request.extensions["timeout"] = {
        "connect": timeout,
        "read": timeout,
        "write": timeout,
        "pool": timeout,
    }

    owns_client = client is None
    active = client or httpx.AsyncClient(
        timeout=httpx.Timeout(timeout), follow_redirects=True
    )
    provenance = "injected-client" if not owns_client else "live"
    try:
        response = await active.send(request)
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError(
            f"{PROVIDER} did not respond within {timeout:g} seconds.",
            provider=PROVIDER,
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderConnectionError(
            f"{PROVIDER} could not be reached. Please try again later.",
            provider=PROVIDER,
        ) from exc
    finally:
        if owns_client:
            await active.aclose()

    classified = classify_http_error(
        response.status_code,
        provider=PROVIDER,
        retry_after=response.headers.get("Retry-After"),
    )
    if classified is not None:
        raise classified

    text = _parse_space_response(response.text, key=key)
    return OcrResult(
        text=text,
        engine=_engine_label(url),
        provenance=provenance,
        provider=PROVIDER_OCR_SPACE,
    )


async def run_ocr(
    image: bytes,
    *,
    filename: str = "upload",
    content_type: str = "application/octet-stream",
    client: httpx.AsyncClient | None = None,
    environ: Mapping[str, str] | None = None,
) -> OcrResult:
    """Send ``image`` to the configured OCR endpoint and return its text.

    ``client`` is the fixture seam: tests inject an
    ``httpx.AsyncClient(httpx.MockTransport(...))`` and the result is
    tagged ``provenance="injected-client"``. When omitted, this function
    owns a real client for the duration of the call (``provenance="live"``).

    ``environ`` overrides where configuration is read (defaults to the
    process environment); B3's demo mode passes an explicit fixture
    mapping so a labelled demo never mutates ``os.environ``.

    Raises
    ------
    ProviderConfigurationError
        The selected provider's variables missing or invalid (``OCR_SPACE_API_KEY``
        for OCR.space, ``OCR_API_URL``/``OCR_API_KEY`` for ``http``, the
        ``GOOGLE_*`` credentials for Google Cloud Vision), a bad ``OCR_TIMEOUT``
        value, an image the selected provider cannot accept, or a misconfigured
        engine/language/limit — raised before any network traffic.
    ProviderTimeoutError / ProviderConnectionError
        The endpoint did not answer / could not be reached.
    ProviderAuthenticationError / ProviderRateLimitError / ProviderServerError
        HTTP 401/403, 429, and 5xx respectively.
    ProviderResponseError
        HTTP 2xx with a body this contract cannot parse.
    """
    if not image:
        raise ValueError("run_ocr requires a non-empty image payload.")

    provider = selected_provider(environ)

    if provider == PROVIDER_GOOGLE:
        owns_google_client = client is None
        text, languages = await run_google_vision(
            image,
            filename=filename,
            content_type=content_type,
            client=client,
            environ=environ,
        )
        return OcrResult(
            text=text,
            engine="vision.googleapis.com",
            provenance="live" if owns_google_client else "injected-client",
            provider=PROVIDER_GOOGLE,
            languages=tuple(languages),
        )

    if provider == PROVIDER_OCR_SPACE:
        return await _run_ocr_space(
            image,
            filename=filename,
            content_type=content_type,
            client=client,
            environ=environ,
        )

    # One consistent, actionable message for a missing configuration. It
    # names the generic adapter's variables *and* the Google Cloud Vision
    # option, so the warning a run carries points at a real fix instead of
    # only the legacy HTTP variables.
    missing_problem = configuration_problem(environ)
    if missing_problem:
        raise ProviderConfigurationError(missing_problem, provider=PROVIDER)
    config = require_env(PROVIDER, ENV_URL, ENV_KEY, environ=environ)
    validate_http_url(PROVIDER, ENV_URL, config[ENV_URL])
    timeout = read_timeout(PROVIDER, ENV_TIMEOUT, default=30.0, environ=environ)

    request = httpx.Request(
        "POST",
        config[ENV_URL],
        headers={"Authorization": f"Bearer {config[ENV_KEY]}"},
        files={IMAGE_FIELD: (filename, image, content_type or "application/octet-stream")},
    )
    # Bound the whole request (connect + read + write) by one timeout.
    request.extensions["timeout"] = {
        "connect": timeout,
        "read": timeout,
        "write": timeout,
        "pool": timeout,
    }

    owns_client = client is None
    active = client or httpx.AsyncClient(
        timeout=httpx.Timeout(timeout), follow_redirects=True
    )
    provenance = "injected-client" if not owns_client else "live"
    try:
        response = await active.send(request)
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError(
            f"{PROVIDER} did not respond within {timeout:g} seconds.",
            provider=PROVIDER,
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderConnectionError(
            f"{PROVIDER} could not be reached. Please try again later.",
            provider=PROVIDER,
        ) from exc
    finally:
        if owns_client:
            await active.aclose()

    classified = classify_http_error(
        response.status_code,
        provider=PROVIDER,
        retry_after=response.headers.get("Retry-After"),
    )
    if classified is not None:
        raise classified

    media_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if media_type == "application/json" or response.text.lstrip().startswith(("{", "[")):
        try:
            payload = json.loads(response.text)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderResponseError(
                f"{PROVIDER} returned a response that is not valid JSON.",
                provider=PROVIDER,
            ) from exc
        text = _extract_text(payload)
        if text is None:
            raise ProviderResponseError(
                f"{PROVIDER} returned JSON without a text field.",
                provider=PROVIDER,
            )
        return OcrResult(
            text=text,
            engine=_engine_label(config[ENV_URL]),
            provenance=provenance,
            provider=PROVIDER_HTTP,
        )

    if media_type and media_type not in _ACCEPTED_MEDIA_TYPES:
        raise ProviderResponseError(
            f"{PROVIDER} returned an unexpected content type "
            f"({media_type or 'unknown'}).",
            provider=PROVIDER,
        )
    try:
        text = response.text
    except UnicodeDecodeError as exc:  # pragma: no cover - httpx decodes leniently
        raise ProviderResponseError(
            f"{PROVIDER} returned a body that is not valid text.",
            provider=PROVIDER,
        ) from exc
    return OcrResult(
        text=text,
        engine=_engine_label(config[ENV_URL]),
        provenance=provenance,
        provider=PROVIDER_HTTP,
    )
