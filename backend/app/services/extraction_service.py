"""D3 — structured extraction: coordinate OCR + vision, emit schema-valid data.

:func:`run_extraction` is the second Module D step B3 calls after
``prepare_image`` (D1). It runs the two provider adapters (D2) over the
same canonical PNG and folds their output into Module B's frozen
contract types.

Degradation policy (deliberate, so a failure can never masquerade as a
clean result):

* **OCR is optional** (Repair R1): when the OCR provider is not
  configured at all, the channel is skipped with an explicit warning and
  extraction continues vision-only — a configuration gap is reported,
  never hidden. Once configured, a *malformed* OCR answer degrades the
  same way as below.
* **Malformed provider output** (``ProviderResponseError`` — the
  provider answered but the payload is unusable) degrades to a
  *warning*: the other channel's output is still returned, and the
  warning states exactly what was lost. Partial results are represented
  as the contract intends (``warnings`` on ``AnalysisResponse``).
* **Transport/config failures of the vision channel** (missing
  credentials, timeout, auth, rate limit, 5xx, connection) **propagate**:
  Module B must report a dependency failure, not a "completed" analysis
  with quietly missing entities.

Provenance: every candidate keeps the extracted spelling, the reported
type, and its sources (``vision``, plus ``ocr`` when the name occurs in
the OCR text). OCR-corroboration is metadata only — no verification
status is ever set here (Module C4 owns evidence evaluation).

Result contract: ``entities`` are always valid
:class:`~app.schemas.entity.AnalysisEntity` objects (ids ``ent-1..N``,
canonical types only), in the same order as ``candidates``.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Mapping, Optional

import httpx
from PIL import Image

from ..integrations import ProviderConfigurationError, ProviderResponseError
from ..integrations.ocr import run_ocr
from ..integrations.vision_model import VisionRelationship, extract_from_image
from ..schemas.entity import AnalysisEntity
from .entity_service import (
    EntityCandidate,
    build_candidates,
    deduplicate,
    to_analysis_entities,
)
from .image_service import PreparedImage

__all__ = ["ExtractionResult", "run_extraction", "vision_payload"]

logger = logging.getLogger(__name__)

#: Largest edge sent to the vision model. Phones shoot 4000px+ photos;
#: re-encoded as PNG a 4953x3302 heritage photo measured 22 MB (29 MB
#: base64 — over most providers' inline limit) and blew the request
#: timeout. Vision models downscale internally anyway, so oversized
#: images are fitted to this bound and re-encoded as JPEG (measured:
#: 749 KB, 17 s instead of 60-90 s).
VISION_MAX_DIMENSION = 2048
#: PNG byte bound: a photo can be within the pixel bound yet still be
#: megabytes once re-encoded as PNG (measured: 1323x2000 -> 2.0 MB PNG
#: vs 358 KB JPEG). Above this size the payload is re-encoded as JPEG.
#: Typical screenshots/documents stay under it and are forwarded
#: byte-for-byte, keeping crisp text for the vision channel.
VISION_MAX_PNG_BYTES = 1_500_000
VISION_JPEG_QUALITY = 85


def vision_payload(png: bytes) -> tuple[bytes, str]:
    """``(bytes, content_type)`` to send to the vision provider.

    Images within both bounds are passed through untouched; anything
    over the pixel or byte bound is fitted to
    :data:`VISION_MAX_DIMENSION` (only when over the pixel bound) and
    encoded as JPEG. OCR always receives the full-resolution PNG —
    recognised text must not lose detail to this optimisation.
    """
    image = Image.open(io.BytesIO(png))
    oversized = max(image.size) > VISION_MAX_DIMENSION
    heavy = len(png) > VISION_MAX_PNG_BYTES
    if not oversized and not heavy:
        return png, "image/png"
    image = image.convert("RGB")
    if oversized:
        image.thumbnail(
            (VISION_MAX_DIMENSION, VISION_MAX_DIMENSION), Image.LANCZOS
        )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=VISION_JPEG_QUALITY, optimize=True)
    return buffer.getvalue(), "image/jpeg"


@dataclass(frozen=True)
class ExtractionResult:
    """Structured extraction output (schema-valid by construction)."""

    #: Recognised text: OCR first, vision-model text as a flagged
    #: fallback, ``None`` when nothing could be read.
    extracted_text: Optional[str]
    #: Schema-valid entities for ``AnalysisResponse.entities``.
    entities: list[AnalysisEntity]
    #: Same order as ``entities``, with provenance attached (D-internal).
    candidates: list[EntityCandidate]
    #: Non-fatal, UI-safe notes about what is uncertain or missing.
    warnings: list[str]
    #: Repair R4 — the vision model's reading of the image (candidate
    #: identification, clues, alternatives, uncertainty). ``None`` when the
    #: vision step failed or returned none; AI-generated, never verified.
    interpretation: Optional[str] = None
    #: The vision model's relationship proposals, passed through untouched
    #: for D4's ``generate_relationship_candidates`` (empty when the
    #: vision step failed or proposed none).
    relationships: list[VisionRelationship] = field(default_factory=list)
    #: True when a *configured* channel answered with unusable output, or
    #: the vision step failed. Drives the run's ``partial`` status: useful
    #: results exist, but a non-critical stage did not succeed.
    degraded: bool = False
    #: True when the vision step produced a validated extraction.
    vision_ok: bool = False
    #: True when OCR was attempted (configured) — whether or not it found
    #: text. A configured provider returning no text is a valid answer.
    ocr_attempted: bool = False
    #: Which OCR provider answered (``google_vision`` | ``http`` | None).
    ocr_provider: Optional[str] = None


async def run_extraction(
    prepared: PreparedImage,
    *,
    filename: str = "upload",
    ocr_client: Optional[httpx.AsyncClient] = None,
    vision_client: Optional[httpx.AsyncClient] = None,
    provider_environ: Optional[Mapping[str, str]] = None,
) -> ExtractionResult:
    """Extract text and candidate entities from one prepared image.

    ``ocr_client``/``vision_client`` are the D2 fixture seams (injected
    ``httpx.AsyncClient`` with a ``MockTransport``); omitted, the
    adapters make the real network calls. ``provider_environ`` overrides
    where the adapters read their configuration (B3's demo mode passes
    an explicit fixture mapping so no process environment is mutated).

    Raises any non-``ProviderResponseError`` provider exception
    unchanged (timeout/auth/rate/server/connection/configuration) so the
    orchestrator can fail the analysis honestly.
    """
    png = prepared.to_png_bytes()
    warnings: list[str] = []

    # 1. OCR — recognised text (optional channel; a missing
    #    configuration degrades to a warned skip, never a silent one).
    ocr_text: Optional[str] = None
    ocr_attempted = True
    ocr_provider: Optional[str] = None
    degraded = False
    try:
        ocr = await run_ocr(
            png,
            filename=filename,
            content_type="image/png",
            client=ocr_client,
            environ=provider_environ,
        )
        ocr_provider = ocr.provider
        # ``strip() or None`` is the honest empty case: a provider that
        # found no readable text returns "", never invented text.
        ocr_text = ocr.text.strip() or None
    except ProviderConfigurationError as exc:
        # Names exactly which variables to set (UI-safe by construction).
        ocr_attempted = False
        warnings.append(f"OCR skipped: {exc}")
    except ProviderResponseError:
        degraded = True
        ocr_attempted = True
        warnings.append(
            "OCR returned an unusable response; extraction continued "
            "without recognised text."
        )

    # 2. Vision model — structured entities (+ a text fallback). The
    #    payload is fitted to the provider's practical size budget;
    #    OCR above already ran on the full-resolution image.
    vision = None
    vision_bytes, vision_type = vision_payload(png)
    try:
        vision = await extract_from_image(
            vision_bytes,
            filename=filename,
            content_type=vision_type,
            client=vision_client,
            environ=provider_environ,
        )
    except ProviderResponseError as exc:
        # A 2xx with unusable content is transient in practice (observed
        # live: empty or non-JSON completions from the configured model
        # on roughly a third of calls). Up to two bounded retries are
        # safe: they only fire on a *malformed* answer — auth,
        # rate-limit, timeout and 5xx raise other types and propagate
        # immediately. Never retried into a fabricated result: if every
        # attempt is unusable, the run degrades loudly as before.
        last_response_error: ProviderResponseError = exc
        logger.warning("vision extraction degraded (attempt 1): %s", exc)
        for attempt in (2, 3):
            try:
                vision = await extract_from_image(
                    vision_bytes,
                    filename=filename,
                    content_type=vision_type,
                    client=vision_client,
                    environ=provider_environ,
                )
                break
            except ProviderResponseError as retry_exc:
                last_response_error = retry_exc
                logger.warning(
                    "vision extraction degraded (attempt %d): %s",
                    attempt,
                    retry_exc,
                )
        else:
            logger.warning(
                "vision extraction gave up after 3 attempts: %s",
                last_response_error,
            )
            degraded = True
            warnings.append(
                "The vision model returned an unusable response; no entities "
                "could be extracted."
            )

    # 3. Decide the extracted text, honestly flagging its source.
    if ocr_text is not None:
        extracted_text: Optional[str] = ocr_text
    else:
        vision_text = ((vision.text or "").strip() if vision else "") or None
        if vision_text is not None:
            extracted_text = vision_text
            if ocr_attempted:
                warnings.append(
                    "OCR recognised no text; the extracted text comes from "
                    "the vision model."
                )
            # When OCR was skipped, its "OCR skipped" warning already
            # states that recognised text has no OCR source.
        else:
            extracted_text = None
            if not ocr_attempted:
                warnings.append(
                    "No OCR provider is configured and the vision model "
                    "returned no text."
                )
            elif not warnings:
                warnings.append("No text could be recognised in the image.")

    # 4. Candidates: build (canonicalise types, corroborate with OCR),
    #    then conservatively deduplicate and assign analysis-local ids.
    vision_entities = vision.entities if vision is not None else []
    candidates, build_warnings = build_candidates(vision_entities, ocr_text=ocr_text)
    warnings.extend(build_warnings)

    deduplicated, dedup_warnings = deduplicate(candidates)
    warnings.extend(dedup_warnings)

    if vision is not None and not deduplicated:
        warnings.append("No entities were detected in the image.")

    entities = to_analysis_entities(deduplicated)
    interpretation = None
    if vision is not None and (vision.interpretation or "").strip():
        interpretation = vision.interpretation.strip()
    return ExtractionResult(
        extracted_text=extracted_text,
        entities=entities,
        candidates=deduplicated,
        warnings=warnings,
        interpretation=interpretation,
        relationships=list(vision.relationships) if vision is not None else [],
        degraded=degraded,
        vision_ok=vision is not None,
        ocr_attempted=ocr_attempted,
        ocr_provider=ocr_provider,
    )
