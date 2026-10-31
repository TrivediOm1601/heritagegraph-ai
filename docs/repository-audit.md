# Repository Audit — HeritageGraph AI

**Audit date:** 31 October 2026  
**Branch:** `main`  
**Auditor:** repository cleanup pass (manual inspection + local commands)  
**Scope:** the existing working tree at `F:\Hackathons\IIT GN\HeritageGraphAI`

This audit was produced **before** cleanup changes. No source file, migration, seed,
test, asset or database was modified or deleted during the audit itself.

---

## 1. Executive summary

HeritageGraph AI is a working full-stack application with a FastAPI backend, a
React/Vite frontend, an Alembic-managed SQLite/PostgreSQL schema, and live
integrations for historical search, vision, OCR and mapping. The code and tests
are healthy:

| Check | Baseline result |
|---|---|
| Backend test suite | **677 passed, 6 skipped** (last run before cleanup, 328.9 s) |
| Frontend test suite | **118 passed, 2 skipped** (17 files) |
| TypeScript | `tsc --noEmit` clean |
| Production build | succeeded (976 kB JS / 48 kB CSS) |
| Database | SQLite at revision `0008_heritage_stories`, 22 tables |
| Running app | frontend `200`, backend `/api/health` `200` |

The **largest publication risk is Git state, not code quality**: the repository
has a single commit (`851e9c7`) and a large part of the current application —
Archive Studio, India Heritage Explorer, Stories & Legends, migrations `0006`–
`0008`, their tests and the `LICENSE` — is **untracked**. A clone of the current
commit would not contain the application described by the README. This must be
resolved by the owner in a deliberate commit before publication (see §12).

No real provider credential was found in any tracked file. Local credential
files are correctly ignored.

---

## 2. Original directory structure

Captured before cleanup (generated directories marked `[generated]`):

```text
HeritageGraphAI/
├── .dev-logs/                  [ignored] 1.8 MB of local test/server logs and probe output
├── .freebuff/                  [ignored] tool state
├── .github/workflows/ci.yml    CI definition (untracked)
├── .gitignore
├── .sidecar/                   [ignored] 32 MB local tool index cache
├── .env.example                full environment reference
├── LICENSE                     MIT (untracked)
├── README.md                   92 KB, detailed but missing submission framing
├── REPAIR_PLAN.md              historical repair notes
├── dev-server.log              [ignored] stale Vite log
├── package.json                root setup/dev/test/build scripts
├── backend/
│   ├── .env                    [ignored] local non-secret settings + OCR key
│   ├── .vision_key             [ignored] local vision provider key
│   ├── .adaption_key           [ignored] local Adaption key
│   ├── heritage.db             [ignored] local SQLite, 2 MB
│   ├── .pytest_cache/          [generated]
│   ├── .tmp_archive.db         [generated] test artifact
│   ├── .venv/                  [ignored]
│   ├── app/                    FastAPI application (81 Python modules)
│   ├── scripts/                run_server, probes, image evaluation
│   ├── testdata/               [ignored] 40 MB local evaluation images/reports
│   ├── tests/                  47 pytest files
│   ├── var/                    [ignored] 4.7 MB preserved archive uploads
│   ├── requirements.txt
│   └── pytest.ini
├── database/
│   ├── alembic.ini
│   ├── migrations/versions/    0001 … 0008
│   └── seeds/                  historical records, 67 heritage sites, 18 stories
├── docs/
│   └── HeritageGraph-AI.pdf    earlier project report (28 Oct 2026)
├── frontend/
│   ├── dist/                   [generated] production build
│   ├── node_modules/           [ignored]
│   ├── package.json / lockfile
│   ├── src/                    32 TSX components, 5 pages, services, types
│   ├── tests/                  18 Vitest files
│   └── vite/vitest/tsconfig configuration
├── screenshots/                tracked UI evidence (PNG/WebM) and ignored JSON reports
└── scripts/                    setup, dev, test, browser check, runtime helpers
```

---

## 3. Frontend architecture

- **Framework:** React 19 + TypeScript 5.9, built by Vite 8.
- **Styling:** Tailwind CSS 4 via `@tailwindcss/vite`; one design system in
  `src/styles/index.css`.
- **Routing:** hash-based view switching in `src/app/App.tsx`
  (`Home`, `Archive`, `Map`, `Research`, `History`), with `?q=` used for subject
  routing inside Home.
- **Visualisation:** Cytoscape.js for the knowledge graph; Leaflet 1.9 with
  OpenStreetMap tiles for the India map.
- **API access:** same-origin `/api` only, through `src/services/api.ts` and
  `src/services/archiveApi.ts` / `accountApi.ts`; no client-side API keys.
- **State:** local component state + hooks (`useAnalysis`, `useAuth`,
  `useSubjectRoute`, `useImageFallback`); no Redux/Zustand layer.
- **Tests:** Vitest 5 with Testing Library and jsdom, `fetch` mocked at the
  boundary. `tests/integration.api.test.ts` is opt-in and needs a running backend.

Findings:

| Finding | Severity | Detail |
|---|---|---|
| No unused page/component identified | Informational | All pages are reached from the header; archive/heritage components are imported by their pages |
| `@types/leaflet` is a runtime dependency | Low | Type package belongs in `devDependencies`; harmless but untidy. Not changed to avoid an unnecessary lockfile churn |
| No hardcoded API credentials or production URLs | Pass | Only a localhost warning string exists in `api.ts`; the API base is same-origin |
| Bundle size advisory | Low | Single 976 kB JS chunk (291 kB gzip); Vite warns but the build succeeds. Code-splitting is a future optimisation |

---

## 4. Backend architecture

- **Framework:** FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic, httpx,
  uvicorn; Python 3.11.
- **Entry point:** `backend/app/main.py`; supported launcher
  `python -m scripts.run_server` (loads `.env`, runs migrations/seed when asked).
- **Routers:** health/ready/diagnostics, analysis, search (+ graph), history,
  auth, graph/evidence/media, archives, research, heritage/stories.
- **Services:** analysis orchestration, retrieval, briefing, extraction,
  evidence, relationships, image, auth, history, search graph, export/PDF,
  archive ingestion/search/timeline, document/zip/storage, heritage, stories,
  research.
- **Integrations:** OpenAI-compatible vision endpoint, OCR.space, Google Cloud
  Vision (ADC or API key), Wikipedia/Wikidata/Wikimedia, optional Adaption
  synthesis.
- **Tests:** 47 files covering unit, real migrated SQLite, and an opt-in live
  HTTP layer; provider calls are mocked by default.
- **Configuration:** `app/core/config.py` + process environment; secrets are
  read from local files by `scripts/run_server` where documented.

Findings:

| Finding | Severity | Detail |
|---|---|---|
| Ownership is enforced in SQL | Pass | Archive/file/history/analysis/collection routes filter by the session user; tests cover cross-account `404`s |
| Upload validation is content-based | Pass | Magic-byte sniffing before decode/OCR/vision; size limits from the schema contract |
| ZIP handling rejects traversal/executables/nesting/ratio bombs | Pass | Covered by tests |
| No global API rate limit | Medium | Credential endpoints are rate-limited; search/analysis/archive endpoints rely on size limits and deployment controls. Add a reverse-proxy or application-wide limiter before public launch |
| Background processing is in-process | Medium | Archive files run in FastAPI background tasks, not a durable queue; documented as a scale limit |
| No email verification/password reset | Low | Not implemented; not claimed in the README |
| Google Cloud Vision live call not verified | Low | Adapter and mocked tests exist; this workspace has no GCP credentials. OCR.space is the live-verified provider |

No circular imports or broken imports were found; the application imports and
starts cleanly.

---

## 5. Database architecture

- **Local:** SQLite `backend/heritage.db` (ignored), revision
  `0008_heritage_stories`.
- **Production:** PostgreSQL is supported by configuration, the `psycopg`
  driver and the same Alembic chain; only SQLite was exercised locally.
- **Tables:** 22, including `users`, `user_sessions`, `history_entries`,
  `analysis_runs`, `entities`, `relationships`, `evidence`,
  `historical_records`, `archives`, `archive_files`, `archive_pages`,
  `archive_entities`, `timeline_events`, `research_collections`,
  `research_notes`, `collection_items`, `heritage_sites`, `heritage_stories`,
  `heritage_story_versions`, `heritage_story_sources`.
- **Local contents:** 8 user rows, 21 sessions, 114 analysis runs, 4 archives,
  5 archive files, 67 heritage sites, 18 stories. These are local development
  records and **must not be published**; the database is ignored.
- **Migrations:** additive `0001`–`0008`; `--migrate` is idempotent and
  `--seed` upserts the curated records.

Findings:

| Finding | Severity | Detail |
|---|---|---|
| Local DB is ignored, not tracked | Pass | `.gitignore` covers `*.db`, `backend/heritage.db` |
| No backup/restore automation | Medium | Local SQLite is a file; production requires a managed database backup policy. Documented in `docs/deployment.md` |
| Local DB contains real development accounts | High (if copied) | Never commit or ship `backend/heritage.db`; only curated seeds should be distributed |
| Migration chain is complete | Pass | Fresh initialisation verified through `npm run setup -- --seed` |

---

## 6. Existing integrations

| Integration | Role | State |
|---|---|---|
| MediaWiki / Wikipedia | Text retrieval, subject overview, related topics | Live-capable; key-free; live tests opt-in |
| Wikidata / Wikimedia Commons | Subject type, images, attribution/licence | Live-capable; key-free; media proxy |
| Vision provider (OpenAI-compatible, Gemini endpoint verified) | Image understanding | Configured locally; mocked by default in tests |
| OCR.space | OCR fallback/provider | Configured locally and live-verified earlier |
| Google Cloud Vision | OCR via `files:annotate` / `DOCUMENT_TEXT_DETECTION` | Implemented; mocked tests only; no GCP credentials here |
| Adaption Labs | Optional grounded synthesis | Key present locally but no hosted model; inert by design |
| OpenStreetMap tiles | India Heritage Explorer | Public tiles; attribution shown |
| ReportLab | PDF export | Local, no external service |

No provider key is committed; keys live in ignored local files and are loaded
server-side only.

---

## 7. Test coverage and baseline

- **Backend:** 47 test files; 677 passed, 6 skipped. Skips are opt-in live
  provider/source tests. Tests use real migrated SQLite and mocked provider
  transports.
- **Frontend:** 18 files; 118 passed, 2 skipped (one integration file requires a
  running backend).
- **Browser:** a CDP-based `scripts/browser-check.mjs` drives real Chrome/Edge/
  Brave against the running app; it is not part of CI.
- **Gaps:** no coverage measurement, no lint configuration, no automated
  accessibility audit, no load test, no deployment-environment test. These are
  disclosed rather than claimed.

No failing baseline test was observed. (One combined frontend run during an
earlier verification pass briefly reported a failure that was not reproducible;
five repeated runs of the story/map suites passed. Treat that as a flake to
watch, not a known failure.)

---

## 8. Configuration overview

| Area | Mechanism |
|---|---|
| Backend runtime | `backend/.env` (local, ignored) + process environment |
| Secrets | `backend/.vision_key`, `backend/.adaption_key` (ignored); OCR key currently in `backend/.env` (ignored) |
| Templates | `.env.example`, `backend/.env.example` — placeholders/comments only |
| Frontend | no secrets; same-origin `/api`; optional `VITE_API_BASE_URL` for standalone builds |
| Database | `DATABASE_URL`; local default `sqlite:///heritage.db` |
| Auth | session cookie, PBKDF2 hashes; `SESSION_TTL_HOURS`, `TRUST_PROXY_HEADERS`, `CORS_ORIGINS` |
| Limits | `HG_ZIP_*`, `HG_STORAGE_DIR`, upload contract limits |
| Stories | `STORY_MODERATOR_EMAILS` (empty by default) |
| CI | `.github/workflows/ci.yml`; no secrets, all providers mocked/opt-in |

`.env.example` and `backend/.env.example` were inspected: they contain comments
and placeholders (`<your-vision-key>`, `sqlite:///heritage.db`), not credentials.

---

## 9. Development artifacts

| Path | Size | Tracked? | Assessment |
|---|---|---|---|
| `.dev-logs/` | 1.8 MB | No (ignored) | Local server/test logs; safe to remove. Exact secret-value scan found no provider key inside |
| `frontend/dist/` | 1.1 MB | No (ignored) | Regenerable build output |
| `backend/.pytest_cache/`, `__pycache__/` | small | No | Regenerable |
| `backend/.tmp_archive.db` | 340 KB | No | Test artifact; safe to remove |
| `dev-server.log`, `backend/live-server.log`, `frontend/dev-server.log` | small | No (ignored) | Stale logs |
| `backend/testdata/` | 40 MB | No (backend `.gitignore`) | Evaluation images/reports; re-downloadable, not used by the test suite. Retained to avoid deleting evaluation evidence |
| `.sidecar/` | 32 MB | No (ignored) | Tool index cache; not part of the project |
| `backend/var/` | 4.7 MB | No (ignored) | Preserved archive uploads; runtime data, retained |
| `backend/heritage.db` | 2 MB | No (ignored) | Local database with development accounts; retained, never published |
| `screenshots/*.json` | small | No (ignored) | Browser-check reports; safe to remove |
| `screenshots/*.webm` | 2.4 MB | **Yes** | Existing UI recordings; retained because they are tracked evidence, not newly recorded |

---

## 10. Security risks

| # | Finding | Severity | Status |
|---|---|---|---|
| S1 | Newer application source is untracked while the only commit predates it | **Critical** | Owner must commit the intended source; not a code defect |
| S2 | Local `backend/heritage.db` contains 8 development users/sessions | High if published | Ignored; never copy into a release |
| S3 | Local credential files exist in the working tree | High if published | Ignored and **not tracked**; exact-value scan found no copies in other text files |
| S4 | `.dev-logs` contains local request/server logs | Medium | Ignored; removed during cleanup |
| S5 | No global API rate limit | Medium | Documented; deployment should add a limiter |
| S6 | No CSRF token; same-origin check + SameSite=Lax cookie | Low/Accepted | Covered by tests; suitable for same-origin deployment |
| S7 | No MFA, email verification or password reset | Low | Not claimed |
| S8 | `STORY_MODERATOR_EMAILS` is empty by default | Low | Safe default: no one can approve community stories |
| S9 | CI workflow untested on GitHub | Low | YAML reviewed; commands pass locally; no remote configured |
| S10 | Google Vision path unverified live | Low | Clearly documented; no false claim |

**Secret scan method:** exact-value matching of the contents of
`backend/.vision_key`, `backend/.adaption_key` and the credential values in
`backend/.env` against every file except `.git`, `node_modules`, `.venv`,
`.sidecar`, `.freebuff` and `dist`. Result: matches only in the three credential
files themselves. A regex scan for common key formats also found only
`backend/.vision_key`. No credential appears in any tracked file.

**Recommendation:** if any of those local keys has ever been shared, pasted into
a chat, recorded, or copied outside the machine, rotate it with the provider.
`.gitignore` cannot retract a secret that was exposed elsewhere.

---

## 11. Documentation gaps

Before this pass, `docs/` contained only an earlier PDF report. Missing:
repository audit, cleanup report, technical approach, known limitations,
database schema reference, deployment guide, security policy, contribution
guide and submission checklist. The README is unusually detailed (92 KB) but
lacks the hackathon identity/tagline, a screenshot gallery, a documentation
index and a concise submission-status view. These are addressed in this pass.

The PDF (`docs/HeritageGraph-AI.pdf`, 28 Oct 2026) predates the map and story
features. It is retained as a historical report; the README and new docs are the
current source of truth.

---

## 12. Git state and publication readiness

```text
branch: main
commits: 1  (851e9c7 Import HeritageGraph AI with accounts, cloud OCR, ...)
tracked files: 204
working tree: 37 modified + 43 untracked entries (80 total)
remotes: none configured
```

Confirmed untracked but essential: `.github/`, `LICENSE`, archive/research/
story schemas & services, heritage/story services, migrations `0006`–`0008`,
all new tests, heritage seeds, archive/heritage frontend components/pages,
archive API client and types.

No tracked secrets, `.venv`, `node_modules`, database or build output were
found. Tracked large files are the two WebM UI recordings (2.4 MB total), the
PDF report (985 KB) and the embedded DejaVu fonts (~1.5 MB), all legitimate.

**Required before publication (owner action):** stage and commit the intended
source, tests, migrations, seeds and docs. Suggested logical groups:

1. Application features: archive, heritage map, stories, research + their
   schemas/services/routes and frontend components/pages.
2. Data: migrations `0006`–`0008` and the two JSON seeds.
3. Tests: new backend and frontend suites.
4. Repository: `.github/`, `LICENSE`, docs, README, config examples.

Do not commit `backend/.env`, `.vision_key`, `.adaption_key`, `heritage.db`,
`backend/var/`, `backend/testdata/`, `.sidecar/`, `.freebuff/`, `.dev-logs/`,
`frontend/dist/` or `node_modules/`.

No commit, push or remote configuration was performed in this pass.

---

## 13. Recommended cleanup actions

1. Remove generated caches, stale logs, `frontend/dist`, `.dev-logs` and
   `backend/.tmp_archive.db` (done in the cleanup phase).
2. Keep the local database, archive uploads and evaluation data, but never
   include them in a release.
3. Add the documentation set, tagline, submission status and screenshot gallery.
4. Add `SECURITY.md`, `CONTRIBUTING.md` and `SUBMISSION_CHECKLIST.md`.
5. Re-run backend/frontend tests, typecheck, production build and startup checks
   after cleanup.
6. The owner: review and commit the untracked application before publishing;
   rotate any credential that may have left this machine.
