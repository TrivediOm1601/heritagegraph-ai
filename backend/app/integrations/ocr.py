"""D2 — OCR adapter (env-configured HTTP OCR engine).

One adapter, no vendor assumption: the endpoint, credential and timeout
all come from the environment (see ``app.integrations`` for the full
table), so the *selected* engine can be swapped without touching code
once provider choice and cost approval land.

Wire contract expected from ``OCR_API_URL`` (documented here because no
vendor API is fixed yet):

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

import json
from dataclasses import dataclass, field
from typing import Mapping

import httpx

from . import (
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderError,
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

#: Supported OCR providers. ``google_vision`` uses the official Cloud
#: Vision annotate API (see :mod:`app.integrations.google_vision`);
#: ``http`` is the generic multipart endpoint described below.
PROVIDER_GOOGLE = "google_vision"
PROVIDER_HTTP = "http"
SUPPORTED_PROVIDERS: tuple[str, ...] = (PROVIDER_GOOGLE, PROVIDER_HTTP)

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
    provider (``GOOGLE_APPLICATION_CREDENTIALS``/``GOOGLE_VISION_API_KEY``
    for Google Cloud Vision — never the generic ``OCR_API_URL``).
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

    missing = [
        name for name in (ENV_URL, ENV_KEY) if not (env.get(name) or "").strip()
    ]
    if missing:
        return (
            f"{PROVIDER} is not configured: set "
            + ", ".join(missing)
            + ", or set OCR_PROVIDER=google_vision to use Google Cloud Vision."
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
        ``OCR_API_URL``/``OCR_API_KEY`` missing or invalid, or a bad
        ``OCR_TIMEOUT`` value — raised before any network traffic.
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
