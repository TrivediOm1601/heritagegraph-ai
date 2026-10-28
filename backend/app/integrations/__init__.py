"""Module D — provider integration layer (D2).

Shared vocabulary for the OCR (``ocr.py``) and vision-model
(``vision_model.py``) adapters:

* an exception hierarchy whose messages are written for humans and are
  safe to surface through Module B's canonical error envelope (no URLs,
  no credentials, no response bodies, no stack traces);
* :func:`classify_http_error` — one place that turns an HTTP status into
  the right exception (401/403 auth, 429 rate limit, 5xx server fault,
  everything else unexpected);
* :func:`require_env` / :func:`read_timeout` — environment-driven
  configuration that **reports missing credentials clearly and never
  invents a value** (D2 acceptance criterion).

Configuration contract (chosen by the integration lead for D2 — the
provider itself is still pending cost approval):

======================  ====================  =========================================
Environment variable    Required              Meaning
======================  ====================  =========================================
``OCR_API_URL``         yes                   Full URL of the HTTP OCR endpoint
``OCR_API_KEY``         yes                   Bearer token for the OCR endpoint
``OCR_TIMEOUT``         no (default 30 s)     Request timeout, seconds
``VISION_API_BASE_URL`` yes                   Base URL of an OpenAI-compatible server
                                              (``.../v1``; ``/chat/completions`` is
                                              appended by the adapter)
``VISION_API_KEY``      yes                   Bearer token for the vision endpoint
``VISION_MODEL``        yes                   Model name the provider selected
``VISION_TIMEOUT``      no (default 60 s)     Request timeout, seconds
======================  ====================  =========================================

Intended mapping for Module B's error handler (B3/B2 wire-up, documented
here so the contract is discoverable from one place)::

    ProviderConfigurationError | ProviderAuthenticationError
    ProviderRateLimitError | ProviderTimeoutError
    ProviderConnectionError | ProviderServerError   -> 503 dependency_unavailable

Provider errors are *dependency* failures, never client validation
errors: a request that reaches a provider was already validated.
"""

from __future__ import annotations

import os
from typing import Mapping

__all__ = [
    "ProviderError",
    "ProviderConfigurationError",
    "ProviderAuthenticationError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ProviderConnectionError",
    "ProviderServerError",
    "ProviderResponseError",
    "classify_http_error",
    "require_env",
    "read_timeout",
    "validate_http_url",
    "DEFAULT_TIMEOUT_SECONDS",
]

#: Fallback request timeout when no ``*_TIMEOUT`` variable is set.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Upper bound for a configured timeout — an absurd value is a typo, not
#: a plan (a hung provider must surface as a timeout, not a forever-open
#: request).
MAX_TIMEOUT_SECONDS = 600.0


class ProviderError(RuntimeError):
    """Base class for OCR/vision provider failures.

    By construction the message is UI-safe: it names the *variable* or
    the *condition*, never the credential value, endpoint URL, response
    body or traceback. The original exception stays chained
    (``raise ... from exc``) for the server log.
    """

    def __init__(self, message: str, *, provider: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        #: Seconds the caller should wait before retrying (429 with a
        #: ``Retry-After`` header), otherwise ``None``.
        self.retry_after = retry_after


class ProviderConfigurationError(ProviderError):
    """Required configuration (credentials/endpoint) is missing or invalid."""


class ProviderAuthenticationError(ProviderError):
    """The provider rejected our credentials (HTTP 401/403)."""


class ProviderRateLimitError(ProviderError):
    """The provider rate-limited or quota-limited the request (HTTP 429)."""


class ProviderTimeoutError(ProviderError):
    """The provider did not answer within the configured timeout."""


class ProviderConnectionError(ProviderError):
    """The provider could not be reached at all (DNS/TCP/TLS failure)."""


class ProviderServerError(ProviderError):
    """The provider itself failed (HTTP 5xx)."""


class ProviderResponseError(ProviderError):
    """The provider answered, but the payload is malformed or unusable.

    Raised instead of guessing: a malformed body must never be silently
    turned into a plausible-looking empty result.
    """


def classify_http_error(
    status_code: int,
    *,
    provider: str,
    retry_after: str | None = None,
) -> ProviderError | None:
    """Map an HTTP status onto a provider exception (``None`` when 2xx/3xx).

    ``retry_after`` is the raw ``Retry-After`` header value; it is parsed
    best-effort (numeric seconds only — HTTP-date forms are ignored
    rather than misparsed).
    """
    if status_code < 400:
        return None

    parsed_retry_after: float | None = None
    if retry_after:
        try:
            candidate = float(retry_after.strip())
        except ValueError:
            parsed_retry_after = None
        else:
            if 0 <= candidate <= MAX_TIMEOUT_SECONDS:
                parsed_retry_after = candidate

    if status_code in (401, 403):
        return ProviderAuthenticationError(
            f"{provider} rejected the configured credentials "
            f"(HTTP {status_code}). Check the API key for this provider.",
            provider=provider,
        )
    if status_code == 429:
        detail = (
            f" Retry after {parsed_retry_after:g} seconds."
            if parsed_retry_after is not None
            else ""
        )
        return ProviderRateLimitError(
            f"{provider} rate-limited the request (HTTP 429).{detail}",
            provider=provider,
            retry_after=parsed_retry_after,
        )
    if status_code >= 500:
        return ProviderServerError(
            f"{provider} failed with a server error (HTTP {status_code}). "
            "Please try again later.",
            provider=provider,
        )
    # Remaining 4xx: our request was wrong in a way the provider
    # reported but we do not model — still a dependency fault from the
    # API consumer's point of view.
    return ProviderError(
        f"{provider} could not process the request (HTTP {status_code}).",
        provider=provider,
    )


def require_env(
    provider: str,
    *names: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Read required environment variables, failing with one clear message.

    All missing names are collected into a single
    :class:`ProviderConfigurationError` so an operator fixes the config
    in one pass. Values are never echoed back (they are secrets by
    definition); blank/whitespace-only counts as missing.
    """
    env = os.environ if environ is None else environ
    values: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            values[name] = value
        else:
            missing.append(name)
    if missing:
        raise ProviderConfigurationError(
            f"{provider} is not configured: set " + ", ".join(missing) + ".",
            provider=provider,
        )
    return values


def read_timeout(
    provider: str,
    name: str,
    *,
    default: float = DEFAULT_TIMEOUT_SECONDS,
    environ: Mapping[str, str] | None = None,
) -> float:
    """Parse a timeout env var into seconds (``default`` when unset).

    A present-but-invalid value is an error, not a silent fallback —
    the same fail-fast rule B1 applies to ``PORT``/``LOG_LEVEL``.
    """
    env = os.environ if environ is None else environ
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        seconds = float(raw)
    except ValueError:
        raise ProviderConfigurationError(
            f"{provider} is misconfigured: {name} must be a number of "
            f"seconds (got {raw!r}).",
            provider=provider,
        ) from None
    if not 0 < seconds <= MAX_TIMEOUT_SECONDS:
        raise ProviderConfigurationError(
            f"{provider} is misconfigured: {name} must be between 0 and "
            f"{MAX_TIMEOUT_SECONDS:g} seconds (got {seconds:g}).",
            provider=provider,
        )
    return seconds


def validate_http_url(provider: str, name: str, url: str) -> str:
    """Check that a configured URL is an absolute http(s) URL."""
    if not url.lower().startswith(("http://", "https://")):
        raise ProviderConfigurationError(
            f"{provider} is misconfigured: {name} must be an http(s) URL.",
            provider=provider,
        )
    return url
