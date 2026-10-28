"""B3 — analysis orchestration (Module D processing + Module C persistence).

Implements the required processing order end to end:

1. receive validated upload data (the route runs D1's content gate first);
2. invoke Module D's image preparation and extraction interfaces
   (:func:`~app.services.image_service.prepare_image`,
   :func:`~app.services.extraction_service.run_extraction` → D2 OCR/vision
   adapters);
3. entity normalisation happens inside D3 (shared Module C rule);
4. retrieve historical records through Module C
   (:func:`~app.services.retrieval_service.search_historical_records`);
5. generate relationship candidates through Module D
   (:func:`~app.services.relationship_service.generate_relationship_candidates`);
6. persist entities/candidates through Module C's repositories, then
   evaluate evidence through Module C4
   (:func:`~app.services.evidence_service.evaluate_analysis_evidence`);
7. graph construction/reads are served by B4 from the same persisted rows;
8. analysis metadata is persisted via
   :func:`~app.repositories.analysis_repository` throughout;
9. the final state is returned as the canonical
   :class:`~app.schemas.analysis.AnalysisResponse`.

Failure policy (never a false success):

* provider faults (missing credentials, timeout, auth, rate limit,
  malformed output that could not degrade) → run ``status="failed"``
  with the adapter's UI-safe message;
* database faults → ``status="failed"`` with a sanitised message (the
  repository layer never leaks SQL);
* unexpected exceptions → ``status="failed"`` with a generic message;
  the full traceback goes to the server log only.

**Demo mode (explicit, labelled, opt-in).** With no provider
credentials available — the honest state of this workspace (INT-005) —
``HG_DEMO_MODE=true`` runs the *same pipeline* over fixture
OCR/vision transports and prepends a warning that names it as fixture
output. Demo mode is never implicit: without the flag, a missing
configuration fails *before* a run is queued, with an actionable 503.
Fixture results are never presented as live AI output — the warning is
stored on the run and rendered by the frontend's warning list.

Signature note: ``run_analysis(image_bytes, filename, content_type, ...)``
follows the plan's B3 signature; ``analysis_id``/``prepared`` let the
route hand off a run it already created and an image it already
validated (no double decode).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any, Mapping, Optional, Sequence

import httpx

from ..core.config import ConfigurationError
from ..core.errors import ApiError
from ..core.logging import get_logger
from ..db.session import RepositoryError, session_scope
from ..integrations import (
    ProviderConfigurationError,
    ProviderError,
    ProviderResponseError,
    require_env,
)
from ..integrations.ocr import ENV_KEY as OCR_ENV_KEY
from ..integrations.ocr import ENV_PROVIDER as OCR_ENV_PROVIDER
from ..integrations.ocr import ENV_URL as OCR_ENV_URL
from ..integrations.ocr import PROVIDER as OCR_PROVIDER
from ..integrations.ocr import PROVIDER_HTTP as OCR_PROVIDER_HTTP
from ..integrations.ocr import configuration_problem as ocr_configuration_problem
from ..integrations.vision_model import (
    ENV_BASE_URL as VISION_ENV_BASE_URL,
)
from ..integrations.vision_model import (
    ENV_KEY as VISION_ENV_KEY,
)
from ..integrations.vision_model import (
    ENV_MODEL as VISION_ENV_MODEL,
)
from ..integrations.vision_model import PROVIDER as VISION_PROVIDER
from ..repositories.analysis_repository import (
    attach_entities,
    create_analysis_run,
    get_analysis_run,
    list_analysis_entities,
    update_analysis_run,
)
from ..repositories.entity_repository import normalize_name, upsert_entities
from ..repositories.history_repository import update_by_analysis
from ..repositories.relationship_repository import RelationshipInput, save_relationships
from ..schemas.analysis import AnalysisResponse
from ..schemas.entity import AnalysisEntity
from ..schemas.search import HistoricalBriefing, HistoricalMatch
from .briefing_service import build_briefing
from .evidence_service import evaluate_analysis_evidence
from .extraction_service import run_extraction
from .image_service import PreparedImage, prepare_image
from .relationship_service import generate_relationship_candidates
from .retrieval_service import search_historical_records

logger = get_logger("analysis")

__all__ = [
    "DEMO_ENV_VAR",
    "DEMO_WARNING",
    "demo_mode_enabled",
    "processing_configuration_error",
    "run_analysis",
    "build_analysis_response",
    "terminal_status",
    "NO_USABLE_RESULT",
]

#: Explicit opt-in for fixture-based processing (see module docstring).
DEMO_ENV_VAR = "HG_DEMO_MODE"

#: Stored as the run's first warning whenever demo mode produced the result.
DEMO_WARNING = (
    "DEMO MODE: this analysis used bundled fixture results, not a live "
    "OCR/vision provider. Configure OCR_API_URL, OCR_API_KEY, "
    "VISION_API_BASE_URL, VISION_API_KEY and VISION_MODEL for real "
    "AI processing."
)

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

#: Fixture configuration handed to the adapters in demo mode. The
#: transports never dial out, so these values are inert placeholders —
#: they exist only to satisfy the adapters' "never call without
#: configuration" gate without touching ``os.environ``.
_DEMO_PROVIDER_ENV: dict[str, str] = {
    OCR_ENV_PROVIDER: OCR_PROVIDER_HTTP,
    OCR_ENV_URL: "http://demo-fixture.invalid/ocr",
    OCR_ENV_KEY: "demo-fixture-key",
    VISION_ENV_BASE_URL: "http://demo-fixture.invalid/v1",
    VISION_ENV_KEY: "demo-fixture-key",
    VISION_ENV_MODEL: "demo-fixture-model",
}

#: How many extracted entity names are probed against the record store.
MAX_MATCH_QUERIES = 6
#: Per-name retrieval depth for candidate corroboration / matches.
RETRIEVAL_LIMIT = 8
#: Hard cap on matches returned in the response.
MAX_MATCHES = 8

#: UI-safe message when the database cannot record an analysis at all.
SUBMISSION_UNAVAILABLE = (
    "Analysis submission is unavailable: the database could not be reached."
)
#: UI-safe message for an unexpected mid-run crash.
UNEXPECTED_FAILURE = (
    "The analysis failed due to an unexpected server error. Please try again."
)
DATABASE_FAILURE = (
    "The analysis could not continue because the database was unavailable. "
    "Please try again."
)


def demo_mode_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True only when ``HG_DEMO_MODE`` explicitly opts into fixture output.

    Fixtures are refused outright when ``ENVIRONMENT=production``:
    labelled demo data must never be producible from a production
    deployment, whatever the environment says.
    """
    env = os.environ if environ is None else environ
    if (env.get("ENVIRONMENT") or "").strip().lower() == "production":
        return False
    return (env.get(DEMO_ENV_VAR) or "").strip().lower() in _TRUE_VALUES


def processing_configuration_error() -> Optional[str]:
    """Actionable message when live processing is not configured.

    Returns ``None`` when demo mode is on (fixtures need no credentials),
    when the vision pipeline is fully configured, or when OCR is simply
    *absent* — OCR is an optional second channel (extraction degrades to
    vision-only **with a warning**, never silently). A *partially* set
    OCR configuration is an error: that is a typo, not a choice.
    Never includes values.
    """
    if demo_mode_enabled():
        return None

    problems: list[str] = []
    # Vision is mandatory: it is what turns the image into entities.
    try:
        require_env(
            VISION_PROVIDER,
            VISION_ENV_BASE_URL,
            VISION_ENV_KEY,
            VISION_ENV_MODEL,
        )
    except ProviderConfigurationError as exc:
        problems.append(str(exc))

    # OCR is OPTIONAL. Three states are valid, one is a typo:
    #   * nothing set                     -> OCR disabled (vision-only);
    #   * OCR_PROVIDER=google_vision      -> disabled until credentials exist
    #     (extraction reports an explicit "OCR skipped" warning, never a
    #     silent one, and the run is graded accordingly);
    #   * a fully configured provider     -> must be usable (validated below).
    # A *partially* configured generic endpoint (only one of its two
    # variables) is a typo and fails fast. The message always names the
    # variables belonging to the *selected* provider, so Google Cloud Vision
    # is never asked for OCR_API_URL/OCR_API_KEY.
    provider_raw = (os.environ.get(OCR_ENV_PROVIDER) or "").strip().lower()
    url_set = bool((os.environ.get(OCR_ENV_URL) or "").strip())
    key_set = bool((os.environ.get(OCR_ENV_KEY) or "").strip())
    partially_configured_http = url_set != key_set
    invalid_provider = bool(provider_raw) and provider_raw not in (
        OCR_PROVIDER_HTTP,
        "google_vision",
    )
    if partially_configured_http:
        missing = [n for n in (OCR_ENV_URL, OCR_ENV_KEY) if not (os.environ.get(n) or "").strip()]
        problems.append(
            f"{OCR_PROVIDER} is partially configured: set "
            + ", ".join(missing)
            + " (or unset both OCR variables to run vision-only, "
            "or set OCR_PROVIDER=google_vision to use Google Cloud Vision)."
        )
    elif invalid_provider:
        ocr_problem = ocr_configuration_problem()
        if ocr_problem:
            problems.append(ocr_problem)
    return " ".join(problems) or None


# ---------------------------------------------------------------------------
# Demo fixtures (used ONLY when HG_DEMO_MODE is enabled; labelled as such)
# ---------------------------------------------------------------------------

#: Fixture "OCR" output for the demo document.
_DEMO_OCR_TEXT = (
    "CERTIFICATE OF HISTORICAL RECORD\n"
    "Ada Lovelace and Charles Babbage — the Analytical Engine\n"
    "London, 1843\n"
)

#: Fixture vision payload: entities/relationships named after the curated
#: seed records (so retrieval and evidence find real seeded rows). Types
#: are canonical and every endpoint references an extracted entity, so a
#: demo run shows only its DEMO MODE label — the exclusion/remap paths
#: are covered by the unit suites instead of by confusing demo output.
_DEMO_VISION_PAYLOAD: dict[str, Any] = {
    "text": _DEMO_OCR_TEXT,
    "interpretation": (
        "DEMO FIXTURE — not a real analysis. This labelled payload stands "
        "in for a vision model's reading while no OCR/vision credentials "
        "are configured; configure the providers to analyse real images."
    ),
    "entities": [
        {"name": "Ada Lovelace", "type": "person", "confidence": 0.97},
        {"name": "ada lovelace", "type": "person", "confidence": 0.61},
        {"name": "Charles Babbage", "type": "person", "confidence": 0.93},
        {"name": "Analytical Engine", "type": "event", "confidence": 0.90},
        {"name": "Difference Engine", "type": "event", "confidence": 0.74},
        {"name": "1843", "type": "date", "confidence": 0.85},
        {"name": "Commemorative Plaque", "type": "other", "confidence": 0.66},
    ],
    "relationships": [
        {"source": "Ada Lovelace", "target": "Analytical Engine",
         "label": "wrote notes for", "confidence": 0.92},
        {"source": "Ada Lovelace", "target": "Charles Babbage",
         "label": "collaborated with", "confidence": 0.88},
        {"source": "Charles Babbage", "target": "Difference Engine",
         "label": "designed", "confidence": 0.90},
        {"source": "Ada Lovelace", "target": "1843",
         "label": "active in", "confidence": 0.70},
        {"source": "Ada Lovelace", "target": "Commemorative Plaque",
         "label": "depicted on", "confidence": 0.60},
    ],
}


def _fixture_response(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": json.dumps(payload)}}
            ]
        },
    )


def _demo_clients() -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
    """Fixture transports for demo mode (zero network egress)."""
    ocr = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"text": _DEMO_OCR_TEXT})
        )
    )
    vision = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: _fixture_response(_DEMO_VISION_PAYLOAD))
    )
    return ocr, vision


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


async def _update_run(analysis_id: str, **fields: Any) -> None:
    """One short transaction per lifecycle update (no DB handle held over I/O)."""
    async with session_scope() as session:
        await update_analysis_run(session, analysis_id, **fields)


async def _record_run_start(
    analysis_id: str, *, filename: str, content_type: str, user_id: str | None = None
) -> bool:
    """Ensure a queued run exists; ``False`` when it already progressed."""
    async with session_scope() as session:
        run = await get_analysis_run(session, analysis_id)
        if run is None:
            await create_analysis_run(
                session,
                analysis_id=analysis_id,
                original_filename=filename or None,
                content_type=content_type or None,
                user_id=user_id,
            )
            return True
        return run.status == "queued"


async def _sync_history(
    analysis_id: str, *, status: str, summary: str | None = None
) -> None:
    """Mirror the run's status onto its history row (no-op when absent)."""
    try:
        async with session_scope() as session:
            await update_by_analysis(
                session, analysis_id, status=status, summary=summary
            )
    except Exception as exc:  # noqa: BLE001 — history must never break a run
        logger.warning(
            "analysis %s: could not update its history row: %s",
            analysis_id,
            type(exc).__name__,
        )


async def _retrieve_records(session, entity_names: Sequence[str]):
    """Union of retrieval hits for every entity name (order-stable).

    When the same record is found by several probes, the **highest-
    scoring** hit is kept: the score belongs to the query context that
    produced it, and first-probe-wins used to pin a record at the score
    of an incidental probe (an exact title match could sit at 0.75
    because an earlier entity name matched its content first, dropping
    it out of the capped match list — found in the R2 end-to-end run).
    """
    records: dict[str, Any] = {}
    hits_by_id: dict[str, Any] = {}
    for name in entity_names[:MAX_MATCH_QUERIES]:
        for hit in await search_historical_records(session, name, limit=RETRIEVAL_LIMIT):
            records.setdefault(hit.record.id, hit.record)
            previous = hits_by_id.get(hit.record.id)
            if previous is None or hit.score > previous.score:
                hits_by_id[hit.record.id] = hit
    return records, hits_by_id


def _stored_briefing(run) -> Optional[HistoricalBriefing]:
    """The briefing persisted with the run, re-validated on read.

    A row written by an older build (or a partially written JSON value)
    yields ``None`` rather than a 500: the rest of the analysis is still
    perfectly readable.
    """
    stored = getattr(run, "briefing", None)
    if not isinstance(stored, dict) or not stored:
        return None
    try:
        return HistoricalBriefing(**stored)
    except Exception:  # noqa: BLE001 — stale/incompatible stored shape
        logger.warning("analysis %s has an unreadable stored briefing", run.id)
        return None


def _briefing_query(entities: Sequence[AnalysisEntity]) -> Optional[str]:
    """Fallback subject: the entity the historical explanation is about.

    Kept only for the case where *no* candidate retrieves anything — a
    heritage photograph is normally of a place, so a confidently
    extracted place wins; otherwise the highest-confidence entity.
    ``None`` when nothing was extracted.
    """
    if not entities:
        return None
    places = [entity for entity in entities if entity.type == "place"]
    pool = places or list(entities)
    best = max(pool, key=lambda entity: (entity.confidence or 0.0))
    return best.name


#: Top-confidence entities probed when choosing the briefing subject.
SUBJECT_CANDIDATES = 4


async def _briefing_subject(
    session, entities: Sequence[AnalysisEntity]
) -> tuple[Optional[str], list]:
    """Pick the briefing subject by *retrieval evidence*, not by type.

    The old rule ("prefer any place") was measured against real images
    and mis-fired: a photograph of a Gutenberg Bible page briefed the
    city of ``Mainz``, a Nataraja bronze briefed ``Tamil Nadu`` (whose
    top source was an election), and a laptop collage briefed
    ``Wikipedia`` — broad container entities outranked the actual
    subject. Each of the top :data:`SUBJECT_CANDIDATES` candidates is
    now probed against the real record store; only candidates the store
    actually knows are eligible, and they are ranked by
    ``(confidence, exact title match, top score, specificity)`` — the
    explanation is about the thing the model is most confident the image
    depicts, provided retrieval can back it up, with ties broken toward
    an exact record of that name.

    Returns ``(query, hits)``; ``([], …)`` when nothing retrieves, in
    which case the caller falls back to the heuristic and the briefing
    honestly reports having no relevant source.
    """
    if not entities:
        return None, []
    ranked = sorted(
        entities, key=lambda entity: (entity.confidence or 0.0), reverse=True
    )[:SUBJECT_CANDIDATES]

    best_query: Optional[str] = None
    best_hits: list = []
    best_key: tuple = ()
    for entity in ranked:
        hits = await search_historical_records(
            session, entity.name, limit=RETRIEVAL_LIMIT
        )
        if not hits:
            continue
        top = max(hits, key=lambda hit: hit.score)
        exact = int(normalize_name(top.record.title) == normalize_name(entity.name))
        key = (
            round(entity.confidence or 0.0, 4),
            exact,
            round(top.score, 4),
            len(entity.name.split()),
        )
        if key > best_key:
            best_query, best_hits, best_key = entity.name, hits, key

    if best_query is not None:
        return best_query, best_hits
    return _briefing_query(entities), []


async def build_analysis_response(session, run) -> AnalysisResponse:
    """Canonical response for one persisted run (read side for GET).

    Entities/matches appear only once rows exist; queued/processing runs
    report lifecycle fields only. Matches are recomputed from the real
    record store with the same retrieval service the pipeline used, so
    they are always query-dependent, traceable data — never a stored
    fabrication. The briefing is *stored* at completion (quoting sources)
    so polling never re-fetches them.
    """
    entities: Optional[list[AnalysisEntity]] = None
    matches: Optional[list[HistoricalMatch]] = None

    links = await list_analysis_entities(session, run.id)
    if links:
        entities = [
            AnalysisEntity(
                id=entity.id,
                name=entity.name,
                type=entity.entity_type,
                confidence=link.confidence,
            )
            for entity, link in links
        ]
        if run.status in ("completed", "partial", "failed"):
            _, hits_by_id = await _retrieve_records(
                session, [entity.name for entity, _ in links]
            )
            ranked = sorted(
                hits_by_id.values(),
                key=lambda hit: (-(hit.to_match().get("score") or 0.0), hit.record.title),
            )
            matches = [HistoricalMatch(**hit.to_match()) for hit in ranked[:MAX_MATCHES]]

    return AnalysisResponse(
        id=run.id,
        status=run.status,
        progress=run.progress,
        extracted_text=run.extracted_text,
        interpretation=run.interpretation,
        entities=entities,
        matches=matches,
        briefing=_stored_briefing(run),
        warnings=list(run.warnings) if run.warnings else None,
        error=run.error,
    )


#: UI-safe message when neither channel produced anything usable.
NO_USABLE_RESULT = (
    "The vision model could not analyse this image and no text could be "
    "extracted, so no analysis result could be produced. Please try again."
)


def terminal_status(extraction) -> str:
    """Grade one extraction honestly (``completed`` | ``partial`` | ``failed``).

    * ``failed``  — the required vision stage produced nothing usable AND
      no text was extracted: there is no result to show.
    * ``partial`` — either a non-critical stage failed (vision unusable
      with OCR text surviving, or a configured OCR channel errored), **or**
      the pipeline ran but produced nothing at all (no entities *and* no
      text). Reporting that as ``completed`` is the false success this rule
      exists to prevent.
    * ``completed`` — the required stages succeeded and produced a usable
      result. An image with no readable text (OCR returns empty) but with
      identified entities is still a complete analysis.

    A *missing* OCR configuration alone is not a degradation: OCR is an
    optional channel, reported through an explicit warning instead.
    """
    has_text = bool((extraction.extracted_text or "").strip())
    if not extraction.vision_ok:
        return "partial" if has_text else "failed"
    if extraction.degraded:
        return "partial"
    if not extraction.entities and not has_text:
        # The pipeline completed but found nothing to show: do not claim a
        # successful analysis of content it never identified.
        return "partial"
    return "completed"


def _failure_message(exc: BaseException) -> str:
    """UI-safe failure text for the run's ``error`` field."""
    if isinstance(exc, (ProviderError, ApiError, RepositoryError, ConfigurationError)):
        # Each of these is sanitised by construction (no SQL, no URLs,
        # no credentials, no tracebacks) — surfaced as-is.
        return str(exc)
    if isinstance(exc, ValueError):
        return str(exc)
    return UNEXPECTED_FAILURE


# ---------------------------------------------------------------------------
# The workflow
# ---------------------------------------------------------------------------


async def run_analysis(
    image_bytes: bytes,
    filename: str,
    content_type: str,
    *,
    analysis_id: Optional[str] = None,
    prepared: Optional[PreparedImage] = None,
    user_id: Optional[str] = None,
) -> AnalysisResponse:
    """Run the full analysis workflow and return the final response.

    Creates/uses the run row (``analysis_id`` lets the route queue first
    and hand off), processes through Modules D and C in the required
    order, and always leaves the run in a terminal or in-progress state —
    a failure is recorded as ``status="failed"`` with a safe message,
    never returned as success.

    Directly invokable (the agreed service interface): calling it with
    only ``(image_bytes, filename, content_type)`` creates its own run.
    """
    run_id = analysis_id or str(uuid.uuid4())
    started = await _record_run_start(
        run_id, filename=filename, content_type=content_type, user_id=user_id
    )
    if not started:
        # Already processing/completed/failed — never double-run a run id.
        async with session_scope() as session:
            run = await get_analysis_run(session, run_id)
            return await build_analysis_response(session, run)

    warnings_so_far: list[str] = []
    try:
        await _update_run(run_id, status="processing", progress=0.05)

        # --- 2/3. Module D: preparation (unless pre-validated) + extraction
        if prepared is None:
            prepared = await asyncio.to_thread(
                prepare_image, image_bytes, filename=filename, content_type=content_type
            )
        demo = demo_mode_enabled()
        ocr_client: Optional[httpx.AsyncClient] = None
        vision_client: Optional[httpx.AsyncClient] = None
        if demo:
            ocr_client, vision_client = _demo_clients()
            warnings_so_far.append(DEMO_WARNING)
            logger.info("analysis %s running in HG_DEMO_MODE (fixture providers)", run_id)
        extraction = await run_extraction(
            prepared,
            filename=filename,
            ocr_client=ocr_client,
            vision_client=vision_client,
            provider_environ=_DEMO_PROVIDER_ENV if demo else None,
        )
        warnings_so_far.extend(extraction.warnings)

        # Grade the extraction before doing anything expensive: a run with
        # no usable result must not be marked completed just because the
        # request reached the end of the pipeline.
        outcome = terminal_status(extraction)
        if outcome == "failed":
            raise ProviderResponseError(
                NO_USABLE_RESULT, provider="Analysis pipeline"
            )

        await _update_run(run_id, progress=0.45)

        # --- 4. Module C retrieval (records feed candidates + matches) ---
        async with session_scope() as session:
            records, _ = await _retrieve_records(
                session, [entity.name for entity in extraction.entities]
            )

        await _update_run(run_id, progress=0.60)

        # --- 4b. Grounded historical explanation (briefing service) --------
        # Retrieval alone gives the user titles and links; the explanation is
        # built from the *same* records' own text, with citation indexes.
        # The subject is chosen by retrieval evidence (see
        # ``_briefing_subject``), so the explanation is about the thing the
        # record store actually knows rather than a broad container name.
        # A failure here degrades the answer to matches-only, never the run.
        briefing: Optional[HistoricalBriefing] = None
        async with session_scope() as session:
            briefing_query, briefing_hits = await _briefing_subject(
                session, extraction.entities
            )
        if briefing_query:
            try:
                briefing = await build_briefing(
                    briefing_query,
                    briefing_hits,
                    extra_uncertainty=(
                        f"This explanation describes the retrieved record for "
                        f"\u201c{briefing_query}\u201d. It does not confirm that the "
                        f"uploaded image depicts it \u2014 that identification is the "
                        f"vision model's candidate.",
                    ),
                )
            except Exception as exc:  # noqa: BLE001 — enrichment only
                logger.warning(
                    "analysis %s: briefing unavailable: %s", run_id, type(exc).__name__
                )
                warnings_so_far.append(
                    "The historical explanation could not be built from the "
                    "retrieved sources; the matched records are still listed."
                )

        # --- 5. Module D relationship candidates (provenance vs records) --
        candidates, rel_warnings = generate_relationship_candidates(
            extraction.relationships,
            extraction.entities,
            records=list(records.values()),
        )
        warnings_so_far.extend(rel_warnings)

        # --- 6a. persist entities, links, candidates (Module C) ----------
        async with session_scope() as session:
            db_entities = await upsert_entities(
                session,
                [
                    {"name": entity.name, "entity_type": entity.type}
                    for entity in extraction.entities
                ],
            )
            # upsert preserves input order for unique (name, type) inputs
            # (D3 already guarantees uniqueness under the shared rule).
            d_to_db = {
                d.id: db_entity.id
                for d, db_entity in zip(extraction.entities, db_entities)
            }
            await attach_entities(
                session,
                run_id,
                [
                    {"entity_id": d_to_db[entity.id], "confidence": entity.confidence}
                    for entity in extraction.entities
                ],
            )
            await save_relationships(
                session,
                run_id,
                [
                    RelationshipInput(
                        source_entity_id=d_to_db[candidate.source],
                        target_entity_id=d_to_db[candidate.target],
                        label=candidate.label,
                        verification_status=candidate.verification_status,
                        # NOTE: no relationship_id — the in-run candidate id
                        # (rel-N) is not globally unique, and C1's primary
                        # key is. Module C assigns the persisted id (UUID);
                        # B4's graph/evidence endpoints serve that id.
                        notes=[
                            "candidate: " + candidate.id,
                            "sources: " + ", ".join(candidate.sources),
                            "records: " + (", ".join(candidate.record_ids) or "none"),
                            "confidence: "
                            + (
                                f"{candidate.confidence:.2f}"
                                if candidate.confidence is not None
                                else "unreported"
                            ),
                        ],
                    )
                    for candidate in candidates
                ],
            )
            await update_analysis_run(
                session,
                run_id,
                extracted_text=extraction.extracted_text,
                interpretation=extraction.interpretation,
                briefing=(
                    briefing.model_dump(mode="json", by_alias=True)
                    if briefing
                    else None
                ),
                warnings=warnings_so_far,
                progress=0.75,
            )

        # --- 6b. Module C4 evidence evaluation ---------------------------
        async with session_scope() as session:
            evaluations = await evaluate_analysis_evidence(session, run_id)

        # --- 8. terminal state --------------------------------------------
        # ``completed`` only when every required stage succeeded; ``partial``
        # when useful output exists but a non-critical stage did not.
        await _update_run(run_id, status=outcome, progress=1.0)
        await _sync_history(
            run_id,
            status=outcome,
            summary=(
                f"Image analysis of {filename or 'upload'}: "
                f"{len(extraction.entities)} entit"
                f"{'y' if len(extraction.entities) == 1 else 'ies'}, "
                f"{len(candidates)} relationship"
                f"{'y' if len(candidates) == 1 else 'ies'}."
                + (" Some processing stages were degraded." if outcome == "partial" else "")
            ),
        )
        logger.info(
            "analysis %s %s: %d entities, %d relationships (%s)",
            run_id,
            outcome,
            len(extraction.entities),
            len(candidates),
            ", ".join(sorted({evaluation.status for evaluation in evaluations}))
            or "no edges",
        )

        async with session_scope() as session:
            run = await get_analysis_run(session, run_id)
            return await build_analysis_response(session, run)

    except Exception as exc:  # noqa: BLE001 — the boundary must record *every* failure
        message = _failure_message(exc)
        # Full detail (chained causes included) goes to the SERVER LOG only
        # — the sanitised `message` is what reaches the run/error field.
        logger.error(
            "analysis %s failed: %s: %s",
            run_id,
            type(exc).__name__,
            exc,
            exc_info=exc,
        )
        try:
            await _update_run(
                run_id,
                status="failed",
                error=message,
                warnings=warnings_so_far or None,
            )
            await _sync_history(run_id, status="failed", summary=message[:360])
            async with session_scope() as session:
                run = await get_analysis_run(session, run_id)
                return await build_analysis_response(session, run)
        except Exception:  # database itself gone — nothing further can be recorded
            logger.exception("analysis %s: could not record the failure", run_id)
            raise exc
