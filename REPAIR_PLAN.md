# HeritageGraph AI — root cause and repair plan

Date: 2026-10-26 · Scope: why every image returns the same Ada Lovelace result,
and what is being repaired.

> **Status (2026-10-27): all repairs R1–R8 implemented and verified.**
> Backend suite **335 passed / 4 skipped**; frontend **44 passed**, typecheck
> clean, production build succeeds; one **real** MediaWiki query verified
> (`HERITAGE_LIVE_SOURCE_TESTS=1`); full upload → process → retrieval →
> evidence → graph journey re-verified over live HTTP with `HG_LIVE_SOURCES=1`,
> including the no-credentials 503 path. What remains blocked is exactly one
> thing: **live OCR/vision credentials** (INT-005) — see the final report.
>
> **Update (same day, image-analysis pass): live vision now works end to end.**
> A real Gemini key was configured (`backend/.vision_key`, git-ignored), the
> pipeline ran against the real provider, and two different heritage photos
> produced two different, source-backed analyses (Rani ki Vav / Qutb Minar) —
> no fixture output, no `DEMO MODE` warning. Suite now **367 passed / 5
> skipped**, and the gated real-provider test passes (`pytest -m live`: 1
> passed, OCR skipped as unconfigured). See §4.

## 1. Root cause (confirmed in code and logs, not assumed)

**Primary cause — the whole pipeline runs on bundled fixtures:**

1. No provider credentials exist in this environment: `env | grep -E "OCR|VISION"`
   returns nothing and there is no `.env` file anywhere in the repository.
2. Because of (1), the documented way to run an analysis is
   `HG_DEMO_MODE=1` (README quickstart, `backend/tests/conftest.py`
   `live_server`). `backend/live-server.log` confirms it:
   `analysis ... running in HG_DEMO_MODE (fixture providers)`.
3. `app/services/analysis_service.py` then swaps the real provider transports
   for `_demo_clients()` (httpx `MockTransport`) whose vision response is the
   constant `_DEMO_VISION_PAYLOAD` — "Ada Lovelace / Charles Babbage /
   Analytical Engine …" — **independent of the uploaded image bytes**. The
   uploaded file is validated and decoded (D1) but never reaches a model.
   Result: every image produces the same entities, matches, relationships and graph.

**The two warnings in the screenshot are fixture artifacts:**

* `"Commemorative Plaque"` is typed `object` in the fixture payload, which is
  outside the canonical vocabulary `person | place | event | date | other`
  (`app/db/models.py::ENTITY_TYPES`) → remapped to `other` with a warning by
  `entity_service.build_candidates`.
* `"Ghost Figure"` is a fixture relationship endpoint that is deliberately not
  an extracted entity → excluded by `relationship_service` with the
  "unknown entities were discarded" warning.

Without `HG_DEMO_MODE`, `POST /api/analysis` correctly refuses with an
actionable 503 — so the app never silently "succeeds" without configuration;
it simply has no configuration to succeed *with*.

**Secondary, genuine defects found while tracing the pipeline (present in live
mode too):**

| # | Defect | Evidence |
|---|--------|----------|
| S1 | Source URLs are **dropped at persistence**: the seed JSON requires and validates `url`, but `HistoricalRecord` has no `url` column, `seed_records()` never stores it, `HistoricalMatch` has no `url` field, and `evidence_service` persists evidence with `source`/`excerpt` only — so `EvidenceItem.url` (which the contract and the DB already support) is never populated and **no source is ever clickable**. | `record_repository.seed_records`, `db/models.HistoricalRecord`, `schemas/search.HistoricalMatch`, `evidence_service.save_evidence(...)` call site |
| S2 | OCR is **mandatory**: a missing `OCR_API_URL`/`OCR_API_KEY` fails the whole analysis even though the vision model also returns text — two providers are required where one would do. | `analysis_service.processing_configuration_error`, `extraction_service.run_extraction` |
| S3 | Retrieval only queries the **20-record local seed**, so a real-world query ("Rani ki Vav") returns nothing relevant regardless of extraction quality. | `retrieval_service.search_historical_records` → `record_repository.search_records` |
| S4 | No heritage-specific extraction instruction: the vision prompt is generic ("historical document or photograph"), so a live model would not be asked for candidate site identity, alternatives or uncertainty. | `vision_model.INSTRUCTION` |

## 2. Repair plan

Ordered by user impact; every item is verified by tests before completion.

1. **R1 — honest configuration gating** (Phase 2/8): demo mode refuses to run
   when `ENVIRONMENT=production`; configuration errors name exactly the
   missing variables; OCR becomes *optional* (explicit warning when absent,
   vision-only extraction), vision stays mandatory. No fixture may ever be
   used outside explicit `HG_DEMO_MODE`.
2. **R2 — real historical sources** (Phase 4): new
   `app/integrations/wikipedia.py` (MediaWiki search API, no key, verified
   live in this workspace) merged into `retrieval_service`, opt-in via
   `HG_LIVE_SOURCES=1`, with timeout, User-Agent, in-process TTL cache and
   graceful degradation (a source outage never fakes results, never breaks
   local search).
3. **R3 — traceable sources end to end** (S1): additive `url` column on
   `historical_records` (migration `0002`), seed URLs persisted, optional
   `url` on `HistoricalMatch` (backend + frontend type), evidence persisted
   with URLs, links rendered in search results, analysis matches and the
   evidence panel.
4. **R4 — heritage-aware extraction** (S4): rewrite the vision instruction to
   request monument/artifact/document description, candidate identifications,
   visible names/dates/inscriptions, alternatives and uncertainty — using only
   the canonical entity types and only referencing extracted entities in
   relationships.
5. **R5 — entity/relationship hygiene** (Phase 5): documented synonym map for
   clear heritage types (`monument`→`place`, …) so valid vocabulary is not
   reported as unknown; conservative approximate endpoint resolution (unique
   punctuation/case-insensitive match) before a proposal is discarded; every
   exclusion still warned about; never an invented entity or edge.
6. **R6 — frontend** (Phase 7): render clickable source URLs, verify status
   lifecycle and that a new upload never shows a previous analysis.
7. **R7 — tests + verification** (Phases 9–11): new backend tests (live-source
   adapter with mock transport, retrieval merge, seed/evidence URLs, config
   gating, approximate resolution), a network-gated live retrieval test that
   really queries Wikipedia ("Rani ki Vav"), full backend + frontend suites,
   and an end-to-end run (demo pipeline + real retrieval) with the failure
   paths (no credentials → 503) re-verified.
8. **R8 — documentation** (Phase 12): README + `.env.example` describe which
   providers are supported, which sources are queried, what is live vs
   fixture, and exactly what credentials must be supplied.

## 3. Blocked on the user

A **live vision/OCR provider call cannot be tested until credentials exist**
(`VISION_API_BASE_URL`, `VISION_API_KEY`, `VISION_MODEL`, optionally
`OCR_API_URL`/`OCR_API_KEY`) plus explicit cost approval. Until then the app
either runs the labelled demo pipeline or refuses with 503 — it will never
present fixture output as real analysis.

## 4. Addendum (2026-10-27, image-analysis repair pass)

**Confirmed the fixture root cause still reproduces exactly as designed:**
`env` holds no `VISION_*`/`OCR_*` variables, no `.env` exists, so the only
way an analysis can run is `HG_DEMO_MODE=1` (see `backend/live-server.log`:
`running in HG_DEMO_MODE (fixture providers)`). Live mode without
credentials answers **503** naming the missing variables — verified, not
silent. The adapters themselves are sound: `vision_model.extract_from_image`
posts the prepared PNG as a base64 `image_url` to
`{VISION_API_BASE_URL}/chat/completions` with `Authorization: Bearer`,
temperature 0, and validates the JSON response into the canonical schema.

**New defect found and fixed (P1): concurrent analyses could fail with
`UNIQUE constraint failed: entities.name, entities.entity_type`.**
`repositories/entity_repository.upsert_entities` used find-then-insert, so
two uploads extracting the same canonical entity at the same time raced;
the loser's run was recorded `failed` ("The operation conflicts with
existing data…"). This intermittently broke the end-to-end HTTP journey
test (2 errors in ~1 of 3 full-suite runs). Fix: the insert now runs in a
SAVEPOINT; on `IntegrityError` the transaction stays usable and the run
adopts the concurrent winner's row (re-raising only if it cannot be found).

**New tests + tooling:**

* `backend/tests/test_live_vision_pipeline.py` — live mode (no demo flag)
  against a loopback OpenAI-compatible stub: asserts the uploaded bytes,
  bearer auth and model id reach the provider, the response — not the
  fixture — becomes the result, no `DEMO MODE` warning appears, 401 and
  unreachable endpoints leave the run `failed`, and two concurrent analyses
  both complete with the same entities. Reproduced the race (failed ~1 in
  2 runs before the fix, 12/12 green after).
* `backend/scripts/vision_smoke.py` — connection check for real
  credentials: `python -m scripts.vision_smoke <image> [--ocr]`
  (exit 0 = provider answered, 2 = configuration missing, 3 = provider
  failed; key values never printed).

Backend suite: **364 passed, 4 skipped** across 4 consecutive full runs
(previously intermittent E2E errors: gone). Frontend: typecheck clean,
48 passed / 2 skipped, production build succeeds.

**Live provider verification (2026-10-27, after credentials arrived):**

* `python -m scripts.vision_smoke testdata/rani-ki-vav.jpg` → exit 0,
  `provenance=live`, correct candidate identification ("Rani ki Vav …")
  with alternatives and uncertainty — the first real vision call in this
  project's history.
* Full HTTP E2E with `HG_LIVE_SOURCES=1`: Rani ki Vav photo →
  `completed`, briefing quoted from the real Wikipedia record with
  citations and uncertainty notes, live matches with non-identical
  scores; Qutb Minar photo → a **different** `completed` result (zero
  shared entities), graph edges `verified` with evidence citing 3 real
  source URLs. A 11.6 MB upload was correctly rejected with 413.
* Browser E2E through the real UI (Vite dev server): upload → preview →
  submit → `Completed`, showing the AI reading labelled "Candidate — not
  a verified identification", 3 entities, the sourced "Historical
  context" briefing, 8 matches with clickable source links and the
  rendered graph — **no DEMO MODE warning anywhere**.
* Two reliability defects found by the real runs and fixed: the 60 s
  vision default failed live uploads (now 120 s), and full-resolution
  photos re-encoded to 22 MB PNGs (now fitted to 2048 px / 1.5 MB and
  JPEG-encoded for the vision request only; OCR keeps full resolution).
  A second photo now completes in ~41 s instead of 60–90 s.
* `pytest -m live` with credentials: **1 passed** (real vision round trip),
  **1 skipped** (OCR unconfigured). conftest now restores host provider
  variables for the gated run — previously the gate could never fire
  because conftest cleared them at import.
---

## 5. Addendum (2026-10-28, R6 — subject photographs, clickable topics, Adaption)

**Goal.** Make search results a real subject overview (photograph, description, key
facts, sources) whose related topics open their own subjects, and verify/integrate the
Adaption provider without weakening the evidence rules.

**Diagnosis of the reported PDF failure.** The "Ada Lovelace fixture for every image"
symptom was already root-caused in §4 (a trailing-junk-tolerant JSON parser for the
vision response). In this pass two further defects were confirmed and fixed:

* `fetch_page_text` could return `None` for a page it had just fetched — an edit-time
  truncation had dropped its final `return page`, so the *first* page fetch of a
  process always "failed" and only a cached second call worked. Caught by
  `tests/test_briefing.py`, fixed, and the suite pins the first-call path again.
* An over-long quoted sentence (Crystal Palace record) made `TimelineEntry`
  validation raise, which the route caught and turned into *no briefing at all*.
  Timeline quotes are now clipped to the contract limit instead of dropping the
  answer (`_clip`).

**Adaption: verified, then integrated honestly.** `scripts/probe_adaption.py` was
added and run against the live service with the supplied key:

* `GET /models` → `200` (auth works);
* `GET /models?type=hosted` → `200 {"object":"list","data":[]}` — the account has
  **no hosted inference model**;
* `POST /responses` → `404 {"message":"Model not found"}` for every id probed
  (including Adaption's own docs example `qwen3.5-397b`) — verified, not assumed.

Adaption's public docs do not document image input for inference, so it was **not**
made the vision provider. It is integrated as the only model allowed to write prose
(`integrations/adaption.py` + `services/synthesis_service.py`), gated behind
`HG_SYNTHESIS=1` and a validated citation contract. With this key it is inert and the
app says so; briefings stay verbatim quotes with citation indexes.

**What was built.**

* `integrations/wikimedia_media.py` — subject photograph + topic thumbnails from
  page-images, plus license/author/file-page from Commons `imageinfo`;
* `integrations/wikidata.py` — canonical entity type from `instance of` (validated
  label table) and navigation-page detection (disambiguation/list pages never become
  topic cards), with a second title-convention signal;
* contract: `briefing.subject`, `briefing.relatedTopics`, `briefing.synthesis`,
  `SearchResponse.notice` (all camelCase, mirrored in `frontend/src/types/api.ts`);
* frontend: `SubjectHeader`/`SubjectImage`/`KeyFacts`/`RelatedTopics`/`SourceList`,
  a `?q=` subject route with real Back/Forward (`hooks/useSubjectRoute.ts`), duplicate
  -paragraph pruning in the briefing panel, and a visible notice when a live source
  was rate-limited instead of a normal-looking empty archive.

**Verification (all measured, this workspace).**

* Backend: **423 passed, 5 skipped** (incl. new suites for media parsing, subject
  profile, topic identity/filtering, the Adaption client and synthesis guard rails).
* Frontend: typecheck clean, **60 passed / 2 skipped**, `vite build` succeeds.
* Live API (new launcher, `HG_LIVE_SOURCES=1`): `Qutub Minar` → subject *Qutb Minar*
  (`place`, CC BY 4.0 photo by Wasir kasab, file page linked) + 4 typed topics with
  thumbnails; `Narendra Modi` → person, portrait (GODL-India / PM's Office) + 3 typed
  topics; `1946` → year briefing with an 8-entry dated timeline. A `429` burst from
  Wikimedia produced `notice` + local fallback, proving degraded ≠ empty.
* Image analysis E2E through the live pipeline: Rani ki Vav photo (4.8 MB) →
  `completed` in 14.9 s, candidate identification, entities, 8 live matches, briefing
  subject *Rani ki Vav* with photograph and topics; OCR warned as unconfigured.
  An 11.6 MB photo was rejected with 413 (limit enforced).
* Browser E2E (Vite dev server + real backend): the subject overview renders the
  photograph with attribution, the quoted explanation, 4 topic cards and 5 numbered
  sources; clicking *Mini Qutub Minar* loaded that subject (heading, image, topics and
  URL all changed, no stale content) and the browser **Back** returned to Qutb Minar.
  A real upload submitted from the page reached `Completed` with the stepwell reading,
  3 entities, 8 matches, graph, "No text could be extracted" (no fictional
  certificate) and **no Ada Lovelace, no DEMO MODE**.
