"""B5 — safe self-diagnosis for the running deployment.

Answers the question the browser cannot answer from a failed request: *is
this backend actually able to analyse an image, and if not, which setting is
missing?* It reports **presence and shape, never values** — no key, token or
credential ever appears in the response, and the provider host is reported
without its path.

Two different questions are kept apart on purpose:

* configuration presence — cheap, always answered by ``GET /api/diagnostics``;
* provider *connectivity* — one authenticated request, made only when the
  caller explicitly asks for it with ``?probe=1`` (and it is a lightweight
  ``GET {base}/models``, never a vision call).

Use it first whenever "the app does not work" is reported; the
``analysis.problems`` list names the exact environment variables to set.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Query, Request

from ...core.logging import get_logger
from ...integrations.google_vision import (
    DEFAULT_ENDPOINT as GOOGLE_VISION_ENDPOINT,
    credentials_configured as google_vision_credentials_configured,
)
from ...integrations.ocr import ENV_KEY as OCR_ENV_KEY
from ...integrations.ocr import ENV_PROVIDER as OCR_ENV_PROVIDER
from ...integrations.ocr import ENV_URL as OCR_ENV_URL
from ...integrations.ocr import (
    PROVIDER_GOOGLE,
    configuration_problem as ocr_configuration_problem,
    selected_provider,
)
from ...integrations.vision_model import ENV_BASE_URL as VISION_ENV_BASE_URL
from ...integrations.vision_model import ENV_KEY as VISION_ENV_KEY
from ...integrations.vision_model import ENV_MODEL as VISION_ENV_MODEL
from ...integrations.wikipedia import live_sources_enabled
from ..routes.health import database_check
from ..routes.media import (
    ALLOWED_IMAGE_HOSTS,
    ENV_USER_AGENT,
    proxy_disabled,
)
from ...services.analysis_service import demo_mode_enabled, processing_configuration_error

router = APIRouter(tags=["diagnostics"])

logger = get_logger("diagnostics")

#: Cheap provider probe timeout (connect + read).
PROBE_TIMEOUT_SECONDS = 8.0

#: Client factory (monkeypatched by tests to inject a mock transport).
_client_factory = httpx.AsyncClient


def _present(environ: Mapping[str, str], name: str) -> bool:
    return bool((environ.get(name) or "").strip())


def _host_of(value: Optional[str]) -> Optional[str]:
    """Hostname only — a base URL may carry a path, never a key (keys are
    sent as headers), but the host alone is what a diagnosis needs."""
    if not value:
        return None
    try:
        return urlsplit(value).hostname or None
    except ValueError:
        return None


def _vision_status(environ: Mapping[str, str]) -> dict[str, Any]:
    model = (environ.get(VISION_ENV_MODEL) or "").strip() or None
    return {
        "configured": all(
            _present(environ, name)
            for name in (VISION_ENV_BASE_URL, VISION_ENV_KEY, VISION_ENV_MODEL)
        ),
        "model": model,
        "endpoint_host": _host_of((environ.get(VISION_ENV_BASE_URL) or "").strip() or None),
    }


def _ocr_status(environ: Mapping[str, str]) -> dict[str, Any]:
    """Report the *selected* OCR provider and whether it can run.

    Presence and shape only: the Google path reports credential *sources*
    (booleans), never key material, and the host of its fixed endpoint.
    """
    try:
        provider = selected_provider(environ)
    except Exception:  # noqa: BLE001 — an invalid value is itself the finding
        return {
            "configured": False,
            "provider": (environ.get(OCR_ENV_PROVIDER) or "").strip() or None,
            "problem": ocr_configuration_problem(environ),
            "endpoint_host": None,
        }

    problem = ocr_configuration_problem(environ)
    if provider == PROVIDER_GOOGLE:
        return {
            # OCR is optional: unconfigured means "vision-only", reported as
            # an explicit warning on the run rather than a failure.
            "configured": problem is None,
            "provider": provider,
            "problem": problem,
            "endpoint_host": _host_of(GOOGLE_VISION_ENDPOINT),
            "credentials": {
                "application_default": google_vision_credentials_configured(environ),
                "api_key": _present(environ, "GOOGLE_VISION_API_KEY"),
                "access_token": _present(environ, "GOOGLE_VISION_ACCESS_TOKEN"),
                "project_set": _present(environ, "GOOGLE_CLOUD_PROJECT"),
            },
        }

    return {
        "configured": problem is None,
        "provider": provider,
        "problem": problem,
        "endpoint_host": _host_of((environ.get(OCR_ENV_URL) or "").strip() or None),
    }


def _synthesis_status(environ: Mapping[str, str]) -> dict[str, Any]:
    requested = (environ.get("HG_SYNTHESIS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    model = (environ.get("ADAPTION_MODEL") or "").strip() or None
    return {
        "requested": requested,
        "configured": _present(environ, "ADAPTION_API_KEY") and bool(model),
        "model": model,
    }


async def _probe_vision(environ: Mapping[str, str]) -> dict[str, Any]:
    """One authenticated request to the configured vision provider.

    Reports the outcome only: the provider's HTTP status and whether the
    request succeeded. Response bodies and credentials are never returned or
    logged — a provider that answers 401 is 'credentials rejected', not a
    leaked payload.
    """
    base = (environ.get(VISION_ENV_BASE_URL) or "").strip()
    key = (environ.get(VISION_ENV_KEY) or "").strip()
    if not base or not key:
        return {
            "target": "vision",
            "ok": False,
            "http_status": None,
            "detail": "Vision is not configured, so there is nothing to probe.",
        }

    url = f"{base.rstrip('/')}/models"
    try:
        async with _client_factory(timeout=PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        logger.warning("vision probe failed: %s", type(exc).__name__)
        return {
            "target": "vision",
            "ok": False,
            "http_status": None,
            "detail": (
                "The provider could not be reached "
                f"({type(exc).__name__}). Check network access and the base URL."
            ),
        }

    ok = 200 <= response.status_code < 300
    if ok:
        detail = "The provider answered and accepted the credentials."
    elif response.status_code in (401, 403):
        detail = "The provider rejected the credentials (check VISION_API_KEY)."
    else:
        detail = f"The provider answered HTTP {response.status_code}."
    return {
        "target": "vision",
        "ok": ok,
        "http_status": response.status_code,
        "detail": detail,
    }


@router.get(
    "/diagnostics",
    summary="Safe configuration and readiness self-check",
)
async def diagnostics(
    request: Request,
    probe: bool = Query(
        False,
        description=(
            "Also make one cheap authenticated request to the vision provider. "
            "Off by default: configuration presence is free, connectivity is not."
        ),
    ),
) -> dict[str, Any]:
    settings = request.app.state.settings
    environ: Mapping[str, str] = os.environ

    database = await database_check(settings)()
    vision = _vision_status(environ)
    ocr = _ocr_status(environ)
    mode = "demo" if demo_mode_enabled(environ) else "live"
    problems = [] if mode == "demo" else [
        problem for problem in (processing_configuration_error() or "").split(". ") if problem
    ]

    payload: dict[str, Any] = {
        "status": "ok" if database.ok and (mode == "demo" or vision["configured"]) else "degraded",
        "service": settings.app_name,
        "environment": settings.environment,
        "configuration": {
            "missing_required": list(settings.missing_required),
            "problems": problems,
        },
        "database": {"configured": bool(settings.database_url), "reachable": database.ok, "detail": database.detail},
        "analysis": {"mode": mode, "vision": vision, "ocr": ocr},
        "sources": {"live": live_sources_enabled(environ)},
        "synthesis": _synthesis_status(environ),
        "media_proxy": {
            "enabled": not proxy_disabled(environ),
            "hosts": sorted(ALLOWED_IMAGE_HOSTS),
            # "configured" means the operator set HG_MEDIA_USER_AGENT to their
            # own contact URL/email; "default" still works but is more likely
            # to be rate-limited by the image host. The value is never echoed.
            "user_agent": (
                "configured" if _present(environ, ENV_USER_AGENT) else "default"
            ),
        },
        "probe": None,
    }

    if probe:
        payload["probe"] = await _probe_vision(environ)

    return payload
