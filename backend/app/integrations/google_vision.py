"""D2 — Google Cloud Vision OCR provider.

Implements the official Cloud Vision ``images:annotate`` REST interface
(https://cloud.google.com/vision/docs/ocr) behind the same ``OcrResult``
contract as the generic HTTP adapter, so the extraction pipeline never
needs to know which provider answered.

Why ``DOCUMENT_TEXT_DETECTION``
-------------------------------
Cloud Vision offers two text features:

* ``TEXT_DETECTION`` — sparse text (signs, plaques, a few words);
* ``DOCUMENT_TEXT_DETECTION`` — dense text and documents, returned as a
  full hierarchical annotation *plus* the flat ``textAnnotations``.

``DOCUMENT_TEXT_DETECTION`` is the superset: it recognises monolithic
document pages (printed historical documents) and multi-region images
(photographs of monuments, plaques, inscriptions) in the same call, which
is exactly the requirement here. Handwriting is **not** assumed to work —
Cloud Vision's handwriting support is best-effort, so unrecognised text
simply yields an empty/short result, and the run reports no recognised
text rather than inventing any.

Authentication
--------------
The adapter supports the supported Google Cloud credential mechanisms,
tried in this order (all server-side only — nothing here is ever sent to
a browser):

1. ``GOOGLE_VISION_ACCESS_TOKEN`` — a short-lived OAuth2 access token
   (useful in CI or when a platform already mints tokens).
2. **Application Default Credentials** via the official ``google-auth``
   library: ``GOOGLE_APPLICATION_CREDENTIALS`` (service-account JSON) or
   the environment's workload/managed identity (GCE/GKE metadata).
   ``GOOGLE_CLOUD_PROJECT`` is used as the quota/billing project when set.
3. ``GOOGLE_VISION_API_KEY`` — a restricted API key (simplest for a local
   demo; restrict it to the Vision API and to this server's egress).

No credential, endpoint or token value is ever included in an exception
message or a log line.
"""

from __future__ import annotations

import json
import os
from typing import Mapping, Optional

import httpx

from . import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
    classify_http_error,
    read_timeout,
    validate_http_url,
)

#: Provider label used in exception messages (never a URL or key).
PROVIDER = "Google Cloud Vision"

ENV_PROVIDER = "OCR_PROVIDER"
ENV_ACCESS_TOKEN = "GOOGLE_VISION_ACCESS_TOKEN"
ENV_API_KEY = "GOOGLE_VISION_API_KEY"
ENV_PROJECT = "GOOGLE_CLOUD_PROJECT"
ENV_CREDENTIALS = "GOOGLE_APPLICATION_CREDENTIALS"
ENV_ENDPOINT = "GOOGLE_VISION_ENDPOINT"
ENV_TIMEOUT = "OCR_TIMEOUT"

#: Default endpoint. Overridable only for an emulator/proxy; the value is
#: validated as http(s) before use.
DEFAULT_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"

#: The documented dense-text feature (superset of TEXT_DETECTION).
FEATURE_TYPE = "DOCUMENT_TEXT_DETECTION"

#: Vision API language hints are optional; leaving them unset lets the
#: service auto-detect, which is what multi-script heritage material needs.

#: How many detected languages we surface as metadata (never as text).
MAX_LANGUAGE_HINTS = 8


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _value(environ: Mapping[str, str] | None, name: str) -> str:
    return (_env(environ).get(name) or "").strip()


def credentials_configured(environ: Mapping[str, str] | None = None) -> bool:
    """True when *some* supported Google credential source is present.

    Presence only — nothing is validated (or contacted) here, so this is
    safe to call from readiness/diagnostic code paths.
    """
    if _value(environ, ENV_ACCESS_TOKEN) or _value(environ, ENV_API_KEY):
        return True
    credentials_file = _value(environ, ENV_CREDENTIALS)
    if credentials_file:
        return True
    # Workload / managed identity: the google-auth library resolves these
    # from the metadata server without any variable being set.
    return bool(
        _value(environ, "GOOGLE_GHA_CREDS_PATH")
        or _value(environ, "K_SERVICE")
        or _value(environ, "GOOGLE_CLOUD_PROJECT")
    )


def _access_token(environ: Mapping[str, str] | None) -> Optional[str]:
    """Mint/fetch an OAuth2 token through Application Default Credentials.

    Returns ``None`` when google-auth is not installed or ADC is not
    configured, letting the caller fall through to the next mechanism.
    The ``google-auth`` import is deliberately lazy: the package is an
    optional dependency for deployments that use an API key instead.
    """
    try:
        import google.auth  # type: ignore[import-not-found]
        from google.auth.transport.requests import (  # type: ignore[import-not-found]
            Request as GoogleAuthRequest,
        )
    except Exception:  # noqa: BLE001 — optional dependency
        return None

    try:
        scopes = ["https://www.googleapis.com/auth/cloud-vision"]
        credentials, _project = google.auth.default(scopes=scopes)
        credentials.refresh(GoogleAuthRequest())
        token = getattr(credentials, "token", None)
        return token or None
    except Exception as exc:  # noqa: BLE001 — ADC not available/denied
        raise ProviderConfigurationError(
            f"{PROVIDER} is not configured: application default credentials "
            "could not be obtained. Set GOOGLE_APPLICATION_CREDENTIALS to a "
            "service-account JSON file, run on a workload with an attached "
            "identity, or set GOOGLE_VISION_API_KEY.",
            provider=PROVIDER,
        ) from exc


def _auth(
    environ: Mapping[str, str] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build ``(headers, query_params)`` for one request.

    Never returns or logs the credential value anywhere else.
    """
    token = _value(environ, ENV_ACCESS_TOKEN)
    if token:
        return {"Authorization": f"Bearer {token}"}, {}

    api_key = _value(environ, ENV_API_KEY)
    if api_key:
        return {}, {"key": api_key}

    adc_token = _access_token(environ)
    if adc_token:
        params: dict[str, str] = {}
        # The quota project is a request parameter, not part of the URL we
        # log, and it is not a secret.
        project = _value(environ, ENV_PROJECT)
        if project:
            params["quotaProject"] = project
        return {"Authorization": f"Bearer {adc_token}"}, params

    raise ProviderConfigurationError(
        f"{PROVIDER} is not configured: set GOOGLE_APPLICATION_CREDENTIALS "
        "(service-account JSON) or GOOGLE_VISION_API_KEY, or set "
        "OCR_PROVIDER=http to use a generic OCR endpoint instead.",
        provider=PROVIDER,
    )


def extract_text(payload: object) -> str:
    """Pull recognised text out of one Cloud Vision annotate response.

    Prefers ``fullTextAnnotation.text`` (structure-preserving, newlines
    kept) and falls back to the first ``textAnnotations`` entry. Returns
    ``""`` when the image genuinely contained no readable text — a valid,
    non-fabricated empty result.
    """
    if not isinstance(payload, dict):
        raise ProviderResponseError(
            f"{PROVIDER} returned a response that is not a JSON object.",
            provider=PROVIDER,
        )

    responses = payload.get("responses")
    if not isinstance(responses, list) or not responses:
        raise ProviderResponseError(
            f"{PROVIDER} returned no annotation results.", provider=PROVIDER
        )

    first = responses[0]
    if not isinstance(first, dict):
        raise ProviderResponseError(
            f"{PROVIDER} returned a malformed annotation result.", provider=PROVIDER
        )

    # A per-image failure arrives as HTTP 200 + responses[0].error.
    error = first.get("error")
    if isinstance(error, dict) and error:
        status = str(error.get("code") or "").strip()
        cleaned = str(error.get("status") or "").strip().upper()
        if cleaned in {"PERMISSION_DENIED", "UNAUTHENTICATED"} or status in {"401", "403"}:
            raise ProviderAuthenticationError(
                f"{PROVIDER} rejected the configured credentials "
                "(permission denied for the Vision API or billing disabled).",
                provider=PROVIDER,
            )
        if cleaned in {"RESOURCE_EXHAUSTED", "QUOTA_EXCEEDED"} or status == "429":
            raise ProviderError(
                f"{PROVIDER} quota or rate limit was exceeded. Please try again later.",
                provider=PROVIDER,
            )
        if status in {"500", "503", "504"}:
            raise ProviderError(
                f"{PROVIDER} failed to process the image. Please try again later.",
                provider=PROVIDER,
            )
        raise ProviderResponseError(
            f"{PROVIDER} could not annotate the image "
            f"({cleaned or 'unknown error'}).",
            provider=PROVIDER,
        )

    full = first.get("fullTextAnnotation")
    if isinstance(full, dict):
        text = full.get("text")
        if isinstance(text, str):
            return text

    annotations = first.get("textAnnotations")
    if isinstance(annotations, list) and annotations:
        head = annotations[0]
        if isinstance(head, dict) and isinstance(head.get("description"), str):
            return head["description"]

    # No text in this image: valid empty result, not an error.
    return ""


def detected_languages(payload: object) -> list[str]:
    """Locale hints reported by the service (metadata only)."""
    if not isinstance(payload, dict):
        return []
    responses = payload.get("responses")
    if not isinstance(responses, list) or not responses:
        return []
    first = responses[0] if isinstance(responses[0], dict) else {}
    full = first.get("fullTextAnnotation")
    pages = full.get("pages") if isinstance(full, dict) else None
    if not isinstance(pages, list):
        return []
    codes: list[str] = []
    for page in pages:
        blocks = page.get("blocks") if isinstance(page, dict) else None
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            languages = block.get("property", {}).get("detectedLanguages") if isinstance(block, dict) else None
            if not isinstance(languages, list):
                continue
            for language in languages:
                code = language.get("languageCode") if isinstance(language, dict) else None
                if isinstance(code, str) and code and code not in codes:
                    codes.append(code)
                    if len(codes) >= MAX_LANGUAGE_HINTS:
                        return codes
    return codes


async def run_google_vision(
    image: bytes,
    *,
    filename: str = "upload",
    content_type: str = "image/png",
    client: httpx.AsyncClient | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Annotate ``image`` and return ``(text, language_hints)``.

    ``client`` is the fixture seam used by the test-suite (an injected
    ``httpx.MockTransport``); when omitted this function owns a real
    network client.

    Raises the shared provider exception hierarchy; a *successful* call
    with no readable text returns ``("", [])``.
    """
    if not image:
        raise ValueError("run_google_vision requires a non-empty image payload.")

    headers, params = _auth(environ)
    endpoint = _value(environ, ENV_ENDPOINT) or DEFAULT_ENDPOINT
    validate_http_url(PROVIDER, ENV_ENDPOINT, endpoint)
    timeout = read_timeout(PROVIDER, ENV_TIMEOUT, default=30.0, environ=environ)

    import base64

    body = {
        "requests": [
            {
                "image": {"content": base64.b64encode(image).decode("ascii")},
                "features": [{"type": FEATURE_TYPE}],
            }
        ]
    }
    request = httpx.Request(
        "POST",
        endpoint,
        headers={**headers, "Content-Type": "application/json"},
        params=params or None,
        json=body,
    )
    request.extensions["timeout"] = {
        "connect": timeout,
        "read": timeout,
        "write": timeout,
        "pool": timeout,
    }

    owns_client = client is None
    active = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout))
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

    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ProviderResponseError(
            f"{PROVIDER} returned a response that is not valid JSON.",
            provider=PROVIDER,
        ) from exc

    text = extract_text(payload)
    return text, detected_languages(payload)
