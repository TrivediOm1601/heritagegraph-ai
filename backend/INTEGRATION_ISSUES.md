# Integration issue list — HeritageGraph AI backend (B5)

Maintained by Member 2 (integration lead). Every entry names the module
that owns the fix and the test that stays **skipped** until it lands.
Skipped tests print these IDs in pytest output (`pytest -rs`).

Status legend: **OPEN** = blocked on another module · **CLOSED** = fixed.

> **Audit update (2026-10-25)** — statuses refreshed after the A/B/C
> integration audit. Module C (C1–C5) landed, B4's read endpoints were
> wired to it, and Module A's error parser was fixed. See the rows below.
>
> **Launch-readiness update (2026-10-26)** — B3 orchestration landed and
> the whole journey is verified end-to-end. `POST /api/analysis` queues a
> run (202), the background pipeline executes D3/D4 → C3/C4 and persists,
> and the frontend polls to completion (the missing poll loop was found
> and fixed in this pass). The shared `live_server` fixture now provisions
> its own migrated + seeded database and runs `HG_DEMO_MODE=1`, so the
> live suite exercises real search/graph/evidence over HTTP. INT-002 and
> INT-003 are closed; INT-004's readiness ping landed (residual: no
> PostgreSQL server in this workspace — SQLite drives everything local).
> Suite: **313 passed, 3 skipped** (the env-gated INT-004/INT-005 live
> variants plus one provider-live test).

| ID | Area | Symptom / current behaviour | Blocking dependency | Owner | Affected tests | Status |
|----|------|-----------------------------|---------------------|-------|----------------|--------|
| INT-001 | Search & evidence data | ~~`GET /api/search` answers 503~~ **CLOSED (audit):** the search and evidence routes are wired to Module C (`search_historical_records`, `list_evidence`); a valid query answers `200` with real records or `[]`, verified live and in `tests/test_routes_wired_db.py`. The two skipped `test_api.py` search tests stay skipped only because the shared `live_server` fixture runs *without* `DATABASE_URL` (they need a seeded live env) | ~~Module C record store~~ (landed) | ~~Module C~~ → closed | `test_api.py` search skips (env-gated, see note) | **CLOSED** |
| INT-002 | Analysis pipeline | ~~`POST /api/analysis` validates, then 503s~~ **CLOSED (2026-10-26):** B3 queues the run (202 + `analysisId`), the background task runs extraction → retrieval → relationships → evidence → persistence, and `GET /api/analysis/{id}` reports queued → processing → completed. Verified live end-to-end (`test_api.py::TestEndToEndHttp`, demo mode) | ~~B3 after C+D~~ (landed) | ~~Member 2 (B3)~~ → closed | `test_api.py::test_upload_is_accepted_and_immediately_pollable`, `test_analysis_journey_completes_with_extraction_and_matches` | **CLOSED** |
| INT-003 | Graph & evidence construction | ~~Construction blocked~~ **CLOSED (2026-10-26):** the full path now runs over HTTP — a completed analysis returns persisted nodes/edges (`test_graph_and_evidence_over_http`), every edge references an existing node, and selecting an edge returns evidence for that exact relationship id with the edge's real verification status; unknown ids stay 404 and cross-analysis reads are refused | ~~Module D + B3~~ (landed) | ~~Module D via B3~~ → closed | `test_api.py::test_graph_and_evidence_over_http` + browser journey | **CLOSED** |
| INT-004 | Database | **Readiness ping CLOSED (2026-10-26):** `/api/ready` now runs an async `SELECT 1` ping through `database_check` and reports `checks.database.ok` (verified live: ready=200 with a configured DB, 503 when the database is down). Residual: no PostgreSQL server in this workspace — SQLite drives the tests and the documented local setup; providing a real `DATABASE_URL` is a deployment step | Deployment config (PostgreSQL URL) for production | Module C + Member 2 → residual only | `test_api.py::test_database_failure_maps_to_a_503` (env-gated); executed equivalents in `test_health.py` + `test_routes_wired_db.py` | **OPEN (residuals only)** |
| INT-005 | Provider failure | No OCR/vision provider or credentials exist (no `.env`, no provider config keys), so provider failures cannot be raised end-to-end. Boundary version passes: a raising check → `/ready` 503 with `Check raised TimeoutError` and no leaked detail | Module D provider integration + provider credentials in config | Module D (Member 4) + Member 2 (config) | `test_api.py::test_provider_failure_maps_to_a_503` (boundary version in `test_health.py::test_provider_timeout_is_reported_without_leaking_details`) | OPEN |
| INT-006 | Frontend ↔ backend origin | ~~No CORS/proxy~~ **CLOSED (audit):** `frontend/vite.config.ts` now proxies `/api` → `http://127.0.0.1:8000` in dev (no CORS changes on either side); browser end-to-end verified live, and `npm run test:integration` passes against a real backend. Production deployments must still serve `/api` same-origin (documented in the config) | ~~Cross-member decision~~ (dev proxy chosen) | ~~Member 1 + Member 2~~ → closed | Frontend `npm run test:integration` (2 tests, passing) | **CLOSED** |
| INT-007 | Error envelope vs Module A parser | ~~`readErrorDetail` reads `error`/`message` as strings~~ **CLOSED (audit):** the parser now unwraps the canonical `{error:{code,message,request_id}}` envelope (legacy string/plain-text shapes still work); covered by `frontend/tests/errorEnvelope.test.ts` (5 tests) and verified in the browser — the UI renders the message text, never raw JSON | ~~Module A parser update~~ | ~~Module A (Member 1)~~ → closed | `frontend/tests/errorEnvelope.test.ts` | **CLOSED** |

## What B5 *does* verify (all passing)

- Full API surface over a **real uvicorn process** with a **real migrated +
  seeded database** and `HG_DEMO_MODE=1`: health, readiness-with-database-ping,
  upload → 202 → background processing → poll-to-completed, every rejection
  path (415/413/422), query-dependent search over seeded records,
  graph + evidence for the correct relationship id, 404 envelope,
  OpenAPI served — `tests/test_api.py` (`@pytest.mark.integration`).
- Module boundary: with C/D absent, all five contract endpoints degrade to
  the identical canonical 503 envelope with no internal leakage.
- Upload validation (invalid upload): `tests/test_upload_validation.py`.
- Readiness aggregation boundary for database/provider failures:
  `tests/test_health.py`.
- Request/response compatibility with the frontend contract:
  `tests/test_openapi.py` (field names, enums, multipart field `file`).

## Reproducible commands

```bash
cd backend

# one-time setup (Windows venv path shown; use .venv/bin/ on POSIX)
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt

# full suite: currently 313 passed, 3 skipped (the env-gated INT-* rows above)
.venv/Scripts/python -m pytest

# show the skip reasons, i.e. the integration issue list in test output
.venv/Scripts/python -m pytest -rs

# layers
.venv/Scripts/python -m pytest -m integration      # live HTTP end-to-end only
.venv/Scripts/python -m pytest -m "not integration"  # fast in-process layers only

# manual end-to-end (this workspace exports PORT=0, hence `env -u PORT`)
env -u PORT .venv/Scripts/python -m uvicorn app.main:app --port 8125
curl http://127.0.0.1:8125/api/health
curl -i http://127.0.0.1:8125/api/ready
curl -i -F "file=@sample.png;type=image/png" http://127.0.0.1:8125/api/analysis
```

Frontend regression (unchanged by B5): `cd frontend && npm test`.
