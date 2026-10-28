"""D2 — vision-model adapter (OpenAI-compatible chat completions).

Sends one prepared image to the configured vision-capable model and
returns **validated structured output**. The endpoint is
``VISION_API_BASE_URL`` + ``/chat/completions`` (the base may already
end in ``/chat/completions``; ``/v1`` style bases work unchanged), so
any OpenAI-compatible server — hosted or local — plugs in through
environment variables alone (see :mod:`app.integrations` for the table).

Request contract:

* ``model`` from ``VISION_MODEL`` (no default is invented — the selected
  model is a team decision),
* ``temperature: 0`` for reproducible extraction,
* one user message with a text instruction demanding a **single JSON
  object** of exactly the shape below, plus the image as a base64
  ``data:`` URL (D1 already guarantees PNG/JPEG/... bytes).

Response contract (validated, never trusted):

.. code-block:: json

    {"text": "optional transcription",
     "interpretation": "candidate reading: what is depicted, clues, alternatives, uncertainty",
     "entities": [{"name": "...", "type": "person", "confidence": 0.9}],
     "relationships": [{"source": "...", "target": "...",
                        "label": "...", "confidence": 0.8}]}

* JSON is extracted from the message content (string or content-part
  list, markdown fences tolerated);
* the envelope must be a JSON object — anything else raises
  :class:`ProviderResponseError`;
* entries are validated with pydantic against the same bounds as
  Module B's canonical schemas (name/label length, confidence clamped to
  0..1); a structurally invalid payload fails loudly instead of being
  coerced into a plausible-looking empty extraction.

Same fixture/live discriminator as ``ocr.run_ocr``: results carry
``provenance="live"`` only when this adapter owns the network client.
"""

from __future__ import annotations

import base64
import json
from typing import Literal, Mapping, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..schemas.analysis import ACCEPTED_IMAGE_TYPES
from . import (
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderResponseError,
    ProviderTimeoutError,
    classify_http_error,
    read_timeout,
    require_env,
    validate_http_url,
)

#: Provider label used in exception messages (never a URL or key).
PROVIDER = "vision service"

ENV_BASE_URL = "VISION_API_BASE_URL"
ENV_KEY = "VISION_API_KEY"
ENV_MODEL = "VISION_MODEL"
ENV_TIMEOUT = "VISION_TIMEOUT"

#: Default request timeout. Measured on a real provider (Gemini free
#: tier): a full-resolution heritage photo takes 60–90 s end to end, so a
#: 60 s budget fails live runs (observed: ``vision service did not
#: respond within 60 seconds``). 120 s keeps large photos inside the
#: budget while still failing fast enough for the frontend's ~3 min
#: poll window. Override with ``VISION_TIMEOUT`` (max 600).
DEFAULT_TIMEOUT_SECONDS = 120.0

#: Path appended to the configured base unless it is already present.
CHAT_COMPLETIONS_SUFFIX = "/chat/completions"

#: Instruction sent with every image. Keep it in sync with the pydantic
#: models below — the model is told the exact field names it must emit.
#: ``type`` advertises only Module C's canonical vocabulary
#: (``app.db.models.ENTITY_TYPES``); D3 additionally canonicalises the
#: response, because the database rejects any other value.
#: (Repair R4: heritage-specific — candidate identification, visual
#: clues, alternatives and uncertainty are requested explicitly, and
#: relationships may only reference entities returned in the same
#: response.)
INSTRUCTION = (
    "You are analysing a photograph of historical heritage material — a "
    "monument, heritage site, inscription, plaque, historical document, "
    "artifact or artwork — for a knowledge-graph pipeline. Respond with "
    "ONE JSON object only (no prose, no markdown fences) of the form: "
    '{"text": "<verbatim text visible in the image — inscription, plaque '
    'or document wording — otherwise null>", '
    '"interpretation": "<your reading of the image: what is depicted; '
    'which heritage site, monument or historical subject it possibly '
    'shows; the visual clues supporting that reading; any alternative '
    'possible identifications; and what is uncertain or missing. Write '
    'candidates as candidates, never as established historical fact. '
    'Max 400 words>", '
    '"entities": [{"name": "<exact text or well-established name>", '
    '"type": "<person|place|event|date|other>", "confidence": <0..1>}], '
    '"relationships": [{"source": "<entity name>", "target": "<entity '
    'name>", "label": "<short predicate>", "confidence": <0..1>}]}. '
    "Entity types: 'place' for monuments, sites, buildings and locations; "
    "'person' for named people; 'event' for dated occurrences; 'date' for "
    "years and periods; 'other' for everything else (artifacts, "
    "inscriptions, documents, dynasties, organisations). Every "
    "relationship source and target must be the exact name of an entity "
    "in this response. Only report what is actually visible or stated in "
    "the image: do not invent names, dates or relationships, omit "
    "entities you cannot read confidently, and lower the confidence and "
    "say so in 'interpretation' when you are unsure."
)


# ---------------------------------------------------------------------------
# Validated model output
# ---------------------------------------------------------------------------


def _clamp_confidence(value: object) -> object:
    """Clamp provider-supplied confidence into the contract's 0..1 range."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return value


class VisionEntity(BaseModel):
    """One entity as reported by the vision model."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=512)
    type: str = Field(min_length=1, max_length=64, default="other")
    confidence: Optional[float] = Field(default=None, ge=0, le=1)

    @field_validator("confidence", mode="before")
    @classmethod
    def _bounds(cls, value: object) -> object:
        return _clamp_confidence(value)


class VisionRelationship(BaseModel):
    """One candidate relationship as reported by the vision model.

    Validation only — evidence evaluation and verification status are
    Module C's (C4) responsibility; nothing here is ever marked
    "verified".
    """

    model_config = ConfigDict(extra="ignore")

    source: str = Field(min_length=1, max_length=512)
    target: str = Field(min_length=1, max_length=512)
    label: Optional[str] = Field(default=None, max_length=512)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)

    @field_validator("confidence", mode="before")
    @classmethod
    def _bounds(cls, value: object) -> object:
        return _clamp_confidence(value)


class VisionExtraction(BaseModel):
    """The validated envelope the vision model must return."""

    model_config = ConfigDict(extra="ignore")

    text: Optional[str] = Field(default=None, max_length=200_000)
    #: Repair R4 — the model's reading: candidate identification(s),
    #: visual clues, alternatives and uncertainty. AI-generated and never
    #: presented as a verified historical claim.
    interpretation: Optional[str] = Field(default=None, max_length=20_000)
    entities: list[VisionEntity] = Field(default_factory=list)
    relationships: list[VisionRelationship] = Field(default_factory=list)
    #: "live" when this adapter owned the network client, otherwise
    #: "injected-client" (test fixture) — the fixture/live discriminator.
    provenance: Literal["live", "injected-client"] = "injected-client"


# ---------------------------------------------------------------------------
# Configuration / request construction
# ---------------------------------------------------------------------------


def chat_completions_url(base_url: str) -> str:
    """Append ``/chat/completions`` unless the base already includes it."""
    base = base_url.rstrip("/")
    if base.endswith(CHAT_COMPLETIONS_SUFFIX):
        return base
    return base + CHAT_COMPLETIONS_SUFFIX


def build_data_url(image: bytes, content_type: str) -> str:
    """Base64 ``data:`` URL for the image, with a sanity-checked MIME."""
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime not in ACCEPTED_IMAGE_TYPES:
        # The image already passed D1's content gate; anything unexpected
        # here means a caller skipped preparation — declare PNG rather
        # than send a type the provider will reject.
        mime = "image/png"
    encoded = base64.b64encode(image).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _message_text(content: object) -> str:
    """Extract text from a chat message content (string or parts list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "".join(chunk for chunk in chunks if isinstance(chunk, str))
    return ""


def _strip_fences(raw: str) -> str:
    """Remove a ```json ... ``` (or bare ```)`` fence if the model added one."""
    stripped = raw.strip()
    if not stripped.startswith("```"):
        return stripped
    first_newline = stripped.find("\n")
    if first_newline == -1:
        return stripped
    body = stripped[first_newline + 1 :]
    end = body.rfind("```")
    if end != -1:
        body = body[:end]
    return body.strip()


def parse_extraction(raw: str) -> dict:
    """Parse the model's message content into a dict for validation.

    Raises :class:`ProviderResponseError` for non-JSON or non-object
    payloads — malformed output is never silently emptied out.
    """
    candidate = _strip_fences(raw)
    if not candidate:
        raise ProviderResponseError(
            f"{PROVIDER} returned an empty response.",
            provider=PROVIDER,
        )
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        # The model sometimes emits a *complete* JSON document followed by
        # stray tokens (observed live: '...}]}}' with an extra brace, and
        # '...}]}}nb}'); json.loads rejects that as "Extra data" and the
        # outermost-{...} span includes the junk. Parse the first complete
        # JSON value instead — this also covers prose-wrapped JSON.
        start = candidate.find("{")
        payload = None
        if start != -1:
            try:
                payload, _ = json.JSONDecoder().raw_decode(candidate, start)
            except json.JSONDecodeError:
                payload = None
        if payload is None:
            # Log a short literal prefix of the malformed completion so a
            # truncation or format shift is diagnosable from server logs.
            snippet = " ".join(candidate.split())[:160]
            raise ProviderResponseError(
                f"{PROVIDER} returned a response that is not valid JSON "
                f"(starts: {snippet!r}).",
                provider=PROVIDER,
            ) from exc
    if not isinstance(payload, dict):
        raise ProviderResponseError(
            f"{PROVIDER} returned JSON that is not an object.",
            provider=PROVIDER,
        )
    return payload


def validate_payload(payload: dict, *, provenance: str) -> VisionExtraction:
    """Validate parsed JSON against the extraction contract."""
    try:
        return VisionExtraction(provenance=provenance, **payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise ProviderResponseError(
            f"{PROVIDER} returned output that failed validation "
            f"({location}: {first.get('msg', 'invalid')}).",
            provider=PROVIDER,
        ) from exc


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------


async def extract_from_image(
    image: bytes,
    *,
    filename: str = "upload",
    content_type: str = "image/png",
    prompt: str = INSTRUCTION,
    client: httpx.AsyncClient | None = None,
    environ: Mapping[str, str] | None = None,
) -> VisionExtraction:
    """Call the configured vision model and return validated extraction data.

    ``client`` is the fixture seam (see ``ocr.run_ocr``): an injected
    client marks the result ``provenance="injected-client"``; omitting it
    makes this function own a real network client (``provenance="live"``).
    ``environ`` overrides where configuration is read (B3's demo mode
    passes an explicit fixture mapping instead of mutating the process
    environment).

    Raises
    ------
    ProviderConfigurationError
        Missing ``VISION_API_BASE_URL`` / ``VISION_API_KEY`` /
        ``VISION_MODEL`` or invalid ``VISION_TIMEOUT`` — before any I/O.
    ProviderTimeoutError / ProviderConnectionError
        No answer / unreachable endpoint.
    ProviderAuthenticationError / ProviderRateLimitError / ProviderServerError
        HTTP 401/403, 429, 5xx.
    ProviderResponseError
        HTTP 2xx whose content is empty, non-JSON, or structurally
        invalid.
    """
    if not image:
        raise ValueError("extract_from_image requires a non-empty image payload.")

    config = require_env(
        PROVIDER, ENV_BASE_URL, ENV_KEY, ENV_MODEL, environ=environ
    )
    validate_http_url(PROVIDER, ENV_BASE_URL, config[ENV_BASE_URL])
    timeout = read_timeout(
        PROVIDER, ENV_TIMEOUT, default=DEFAULT_TIMEOUT_SECONDS, environ=environ
    )

    body = {
        "model": config[ENV_MODEL],
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": build_data_url(image, content_type)},
                    },
                ],
            }
        ],
    }
    request = httpx.Request(
        "POST",
        chat_completions_url(config[ENV_BASE_URL]),
        headers={
            "Authorization": f"Bearer {config[ENV_KEY]}",
            "Content-Type": "application/json",
        },
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

    try:
        envelope = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ProviderResponseError(
            f"{PROVIDER} returned a response that is not valid JSON.",
            provider=PROVIDER,
        ) from exc
    if not isinstance(envelope, dict):
        raise ProviderResponseError(
            f"{PROVIDER} returned JSON that is not an object.",
            provider=PROVIDER,
        )
    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderResponseError(
            f"{PROVIDER} returned no completion choices.",
            provider=PROVIDER,
        )
    first_choice = choices[0]
    message = first_choice.get("message") if isinstance(first_choice, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    raw_text = _message_text(content)
    if not raw_text.strip():
        # Record why the completion is empty (e.g. a safety stop or an
        # interruption) — UI-safe, no payload or credentials involved.
        finish = first_choice.get("finish_reason")
        detail = f" (finish_reason={finish})" if finish else ""
        raise ProviderResponseError(
            f"{PROVIDER} returned a completion with no content{detail}.",
            provider=PROVIDER,
        )

    payload = parse_extraction(raw_text)
    return validate_payload(payload, provenance=provenance)
