"""Adaption Labs — the optional grounded-synthesis provider.

**Why this module exists and what it may be used for.** The briefing's
explanations are *extractive*: every sentence is quoted from a retrieved
source and carries a citation. Adaption is wired in as the one place a
model is allowed to write prose — and only over the retrieved, cited
material (see :mod:`app.services.synthesis_service`). It is never used to
recall history, and it is never used for image understanding: the public
Adaption docs do not document image input for inference, so vision stays
with the vision provider.

**Verified against the live service, not assumed.** Probing this key
(``python -m scripts.probe_adaption``) showed:

* ``Authorization: Bearer <key>`` authenticates — ``GET /models`` and
  ``GET /autoscientist/models`` both answer ``200``;
* the *hosted inference* catalog (``GET /models?type=hosted``) is
  **empty** for this account, and ``POST /responses`` answers
  ``404 {"message":"Model not found"}`` for every id probed, including the
  one in the official docs example;
* the documented inference route is ``POST {base}/responses`` (a
  Responses-style endpoint, **not** OpenAI ``/v1/chat/completions``);
* the response body schema is not documented — the reference types it as a
  generic map — so :func:`extract_text` reads it defensively and raises
  ``ProviderResponseError`` rather than guessing.

Consequence: with the current key, synthesis is **inert**. That is
reported, never hidden — a configured-but-unusable provider produces a
visible note, and the briefing stays the quoted source text.

Configuration::

    ADAPTION_API_KEY      required (secret; backend/.adaption_key is loaded
                          by scripts/run_server.py)
    ADAPTION_MODEL        required (id from GET /models?type=hosted)
    ADAPTION_API_BASE_URL optional, defaults to the documented host
    ADAPTION_TIMEOUT      optional seconds
    HG_SYNTHESIS          opt-in switch; synthesis is off unless this is set
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

import httpx

from . import (
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
    classify_http_error,
    read_timeout,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "ENV_API_KEY",
    "ENV_BASE_URL",
    "ENV_MODEL",
    "ENV_SYNTHESIS",
    "ENV_TIMEOUT",
    "PROVIDER",
    "AdaptionConfig",
    "AdaptionModelUnavailableError",
    "create_response",
    "extract_text",
    "list_hosted_models",
    "load_config",
    "synthesis_enabled",
]

PROVIDER = "Adaption"

ENV_API_KEY = "ADAPTION_API_KEY"
ENV_BASE_URL = "ADAPTION_API_BASE_URL"
ENV_MODEL = "ADAPTION_MODEL"
ENV_TIMEOUT = "ADAPTION_TIMEOUT"
ENV_SYNTHESIS = "HG_SYNTHESIS"

#: Documented host (docs.adaptionlabs.ai — "Generate a model response").
DEFAULT_BASE_URL = "https://api.prod.adaptionlabs.ai/api/v1"

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_OUTPUT_TOKENS = 512

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class AdaptionModelUnavailableError(ProviderConfigurationError):
    """The account has no hosted inference model for the requested id.

    Distinct from a bad key: the credentials worked, the *model* does not
    exist for this organization. The message names the model so an operator
    can list the hosted catalog and set ``ADAPTION_MODEL`` correctly.
    """


@dataclass(frozen=True)
class AdaptionConfig:
    """Resolved Adaption settings (the key is never logged or echoed)."""

    base_url: str
    api_key: str
    model: str


def _env(environ: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def synthesis_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True only when ``HG_SYNTHESIS`` explicitly opts in."""
    value = (_env(environ).get(ENV_SYNTHESIS) or "").strip().lower()
    return value in _TRUE_VALUES


def load_config(environ: Optional[Mapping[str, str]] = None) -> AdaptionConfig:
    """Read the provider configuration, or explain exactly what is missing."""
    env = _env(environ)
    missing = [
        name
        for name in (ENV_API_KEY, ENV_MODEL)
        if not (env.get(name) or "").strip()
    ]
    if missing:
        raise ProviderConfigurationError(
            f"{PROVIDER} is not configured: set " + ", ".join(missing) + ".",
            provider=PROVIDER,
        )
    base_url = (env.get(ENV_BASE_URL) or "").strip() or DEFAULT_BASE_URL
    return AdaptionConfig(
        base_url=base_url.rstrip("/"),
        api_key=(env.get(ENV_API_KEY) or "").strip(),
        model=(env.get(ENV_MODEL) or "").strip(),
    )


def _timeout(environ: Optional[Mapping[str, str]]) -> float:
    return read_timeout(
        PROVIDER, ENV_TIMEOUT, default=DEFAULT_TIMEOUT_SECONDS, environ=environ
    )


def _headers(config: AdaptionConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _classify(status: int, payload: Any, config: AdaptionConfig) -> ProviderError:
    """Classify a non-2xx answer, recognising "model not found" explicitly."""
    message = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "")
        elif isinstance(error, str):
            message = error
        if not message:
            message = str(payload.get("message") or "")
    if status == 404 and "model" in message.casefold():
        return AdaptionModelUnavailableError(
            f"{PROVIDER} has no hosted inference model {config.model!r} for this "
            f"account (HTTP 404). Check GET /models?type=hosted and set "
            f"{ENV_MODEL} to one of the returned ids.",
            provider=PROVIDER,
        )
    return classify_http_error(status, provider=PROVIDER) or ProviderError(
        f"{PROVIDER} could not process the request (HTTP {status}).",
        provider=PROVIDER,
    )


def extract_text(payload: Any) -> Optional[str]:
    """Pull the generated text out of a Responses payload.

    Adaption's reference types the response as a generic map, so every
    documented-plausible carrier is tried (``output_text``, then
    ``output[].content[].text``, then an OpenAI-style ``choices``) and the
    caller is told when none of them carries text — a malformed body must
    never look like an empty-but-successful completion.
    """
    if not isinstance(payload, dict):
        return None

    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for chunk in content:
                    if isinstance(chunk, dict) and isinstance(chunk.get("text"), str):
                        parts.append(chunk["text"])
        joined = "\n".join(part.strip() for part in parts if part.strip())
        if joined:
            return joined

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content.strip():
            return content.strip()

    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return None


async def create_response(
    prompt: str,
    *,
    instructions: Optional[str] = None,
    config: Optional[AdaptionConfig] = None,
    client: Optional[httpx.AsyncClient] = None,
    environ: Optional[Mapping[str, str]] = None,
    max_output_tokens: int = MAX_OUTPUT_TOKENS,
    temperature: float = 0.0,
) -> str:
    """One ``POST /responses`` call; returns the generated text.

    Raises a typed :class:`~app.integrations.ProviderError` subclass on any
    failure — configuration, credentials, rate limit, model unavailable,
    timeout, transport, server error or unusable body. Nothing is retried
    or substituted here: the caller decides what a failure means.
    """
    resolved = config or load_config(environ)
    payload: dict[str, Any] = {
        "model": resolved.model,
        "input": [{"role": "user", "content": prompt}],
        "max_output_tokens": max(1, int(max_output_tokens)),
        "temperature": temperature,
    }
    if instructions:
        payload["instructions"] = instructions

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_timeout(environ))
    try:
        try:
            response = await http.post(
                f"{resolved.base_url}/responses", json=payload, headers=_headers(resolved)
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"{PROVIDER} did not answer in time.", provider=PROVIDER
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderConnectionError(
                f"Could not reach {PROVIDER}.", provider=PROVIDER
            ) from exc

        try:
            body: Any = response.json()
        except ValueError:
            body = None

        if response.status_code >= 400:
            raise _classify(response.status_code, body, resolved)

        text = extract_text(body)
        if not text:
            raise ProviderResponseError(
                f"{PROVIDER} returned an unusable response.", provider=PROVIDER
            )
        return text
    finally:
        if owns_client:
            await http.aclose()


async def list_hosted_models(
    *,
    config: Optional[AdaptionConfig] = None,
    client: Optional[httpx.AsyncClient] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> list[str]:
    """The hosted inference models this account may call.

    ``GET /models?type=hosted`` is the documented hosted catalog (the
    endpoint rejects other ``type`` values, which is how the route was
    identified). An empty list means inference is unavailable for the
    account — the honest answer for this deployment until a model is
    provisioned. Failures raise typed provider errors like
    :func:`create_response`.
    """
    resolved = config or load_config(environ)
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_timeout(environ))
    try:
        try:
            response = await http.get(
                f"{resolved.base_url}/models",
                params={"type": "hosted"},
                headers=_headers(resolved),
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"{PROVIDER} did not answer in time.", provider=PROVIDER
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderConnectionError(
                f"Could not reach {PROVIDER}.", provider=PROVIDER
            ) from exc

        try:
            body: Any = response.json()
        except ValueError:
            body = None
        if response.status_code >= 400:
            raise _classify(response.status_code, body, resolved)

        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            return []
        ids: list[str] = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                ids.append(item["id"])
            elif isinstance(item, str):
                ids.append(item)
        return ids
    finally:
        if owns_client:
            await http.aclose()


def probe_matrix() -> Sequence[str]:
    """Model ids worth probing (documented example + multimodal-labelled)."""
    return ("qwen3.5-397b", "google/gemma-3-27b-it-VLM", "google/gemma-3-4b-it-VLM")
