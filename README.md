# HeritageGraph AI

HeritageGraph AI turns a historical image — or a text query — into evidence-backed
history: what the image shows, what it might be, the historical records that
support (or fail to support) that reading, the entities and relationships
involved, and an interactive knowledge graph where every edge carries an
evidence status.

It is a two-workflow application:

| Workflow | Input | Output |
|---|---|---|
| **Historical text search** | a person, place, monument, event or year | subject overview with photograph, key facts, related topics, cited sources and a knowledge graph |
| **Historical image analysis** | a photograph, scan, inscription or document | visual reading, extracted text, candidate identification, grounded history, entities, relationships, graph, evidence |
| **Account + history** | email + password | private, owner-scoped history of every search and analysis, reopenable later, exportable to PDF |

Everything the app answers with is traceable: explanations quote the retrieved
sources sentence by sentence with citation numbers, and a claim with no
retrieved source is reported as such instead of being filled in.

---

## 1. Project overview

### How text search works

`GET /api/search?query=…` normalises the query, classifies it (year / named
entity / general), retrieves candidates from the local record store **and** —
with `HG_LIVE_SOURCES=1` — from Wikipedia's public APIs, ranks them (exact and
normalised title match, phrase match, full-text, source quality), then builds a
*grounded briefing*: a summary and explanation made of sentences selected from
those records, each carrying the citation index of the record it came from. The
subject's photograph, description, canonical type and the related topics
(thumbnails, types, descriptions) come from the same public sources
(MediaWiki page media + Wikidata), never from a hardcoded list.

### How image analysis works

`POST /api/analysis` accepts the uploaded bytes (≤ 10 MiB, real formats only),
prepares the image, and runs the configured **vision** provider on the actual
image — plus OCR when one is configured. The provider returns structured
observations (description, candidate identification, alternatives,
uncertainty, any legible text). When OCR is configured its text is used for
extraction and the vision model's reading stays a separate, labelled
observation; the two are never presented as one verified inscription. Extracted
entities are then used to retrieve
historical records, the briefing is built from the same records, relationship
candidates are generated and persisted, and each edge is evaluated against the
record store, which assigns its verification status. A run that cannot reach a
provider **fails with a message** — it never returns fixture output. A run whose
required stages succeeded but whose text extraction produced nothing is
reported **partial**, not `completed`.

### How evidence, related topics and the graph work

* **Evidence** — every relationship stores the records that mention its
  endpoints and the verdict computed from them: `verified`, `disputed`,
  `unsupported`, `unverified`. A model's opinion never produces `verified`.
* **Related topics** — real identities (source record id + exact query), so
  clicking one opens *that* subject's overview, photograph, sources and its own
  topics; the URL carries the subject (`?q=…`), so Back/Forward work.
* **Graph** — nodes are the extracted entities, edges the persisted
  relationships with their verification status; selecting an edge shows the
  evidence behind it.
* **Text-search graph** — `GET /api/search/graph?query=…` expresses the *same*
  retrieval as a graph: the queried subject, one node per retrieved record, a
  `retrieved for` edge per record (evidence = that record), and `mentions` edges
  only where one retrieved record's title appears verbatim in another's text,
  with the containing sentence as evidence. Nothing is inferred; a search with
  no records returns an empty graph and says so. The frontend renders it with
  the same Cytoscape component and interaction patterns as the image graph.
* **Accounts and history** — signup/login set an opaque session cookie; every
  search and upload the signed-in user makes is recorded in their own history
  (owner-scoped in SQL, never from a client-supplied id), reopenable from the
  History view and exportable as a PDF.

**Implemented and verified** (see [§8](#8-testing-and-production-build) for the
exact commands and results): search + subject photographs + related-topic
navigation, image upload and live vision analysis, grounded briefings, entities
/relationships/evidence/graph, text-search knowledge graph, accounts
(signup/login/logout/session), owner-scoped history with reopen, server-side PDF
export (single record and full history), same-origin media proxy,
one-command local start, diagnostics endpoint.

**Not verified / not implemented**: a **live** Google Cloud Vision OCR request
(the provider is implemented and covered by mocked-annotate tests, but this
workspace has no GCP credentials, so every run here honestly reports
`OCR skipped`), the optional Adaption synthesis provider (key authenticates but
the account has no hosted model — inert by design, never faked), PostgreSQL
(supported by config/migrations, only SQLite exercised here), any deployed
environment, email verification / password reset (no email delivery is wired
up, so neither is claimed), CI and lint/coverage tooling. These are listed in
full in [§9](#9-troubleshooting) and [§12](#12-engineering-notes).

---

## 2. Features

| Feature | Behaviour |
|---|---|
| Accounts | Signup / login / logout / session (`/api/auth/*`); PBKDF2-SHA256 password hashes, opaque server-side sessions in an HttpOnly cookie, rate-limited credential endpoints |
| Personal history | Every signed-in search and upload is recorded with status, summary, graph and sources; list, reopen, and export — owner-scoped on the backend |
| PDF export | Server-generated, paginated, Unicode-capable PDF of one history record or the whole history, with citations and a text outline of the graph |
| Historical text search | Query validation, live + local retrieval, ranked results, degraded-source notice instead of a false “no results” |
| Text-search knowledge graph | Overview / Related topics / Knowledge graph / Sources tabs; graph built only from retrieved records and verbatim co-mentions |
| Subject photograph | From the subject's own article page image, with author, license and file/source links; placeholder when the source has none |
| Related topics | Identity-backed cards with thumbnails; click opens that subject; keyboard accessible, real links (`?q=…`) |
| Image upload | Browser-side validation (type, size, empty), preview, explicit processing states |
| Image analysis | Real vision provider on the uploaded bytes; candidate identification with stated uncertainty; never assumes a known monument |
| Extracted text | OCR output when configured; the vision model's reading otherwise — the two are never presented as a verified inscription |
| Historical retrieval | Wikipedia/MediaWiki records with canonical URLs; local seeded store as fallback |
| Briefings | Extractive (quoted) explanations with citation numbers, key facts, dated events, plus explicit limitations |
| Entities & relationships | Normalised entities, candidate relationships with provenance, duplicates collapsed, self-loops excluded |
| Evidence panel | Per-edge records evaluated, counts shown including the zero cases |
| Knowledge graph | Cytoscape graph of the analysis, edge selection → evidence |
| Same-origin media proxy | `GET /api/media` serves allowlisted Wikimedia images from the app's own origin (see §9 for why this matters in Brave and other browsers) |
| Self-diagnosis | `GET /api/diagnostics` reports configuration/DB status and, on request, proves provider connectivity without exposing a secret |
| Demo mode | Explicit `--demo` labelled fixture pipeline for demos without credentials; refused under `ENVIRONMENT=production` |

---

## 3. Technology stack

Confirmed by the repository (no dependency is listed that is not actually used):

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript 5.9, Vite 8, Tailwind CSS 4, Cytoscape.js |
| Frontend tests | Vitest 5 + Testing Library (jsdom) |
| Backend | Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic, httpx, uvicorn |
| Backend tests | pytest (unit + database integration + live-HTTP layers) |
| Database | SQLite for development; PostgreSQL supported by config/driver/migrations |
| Image understanding | Any OpenAI-compatible vision endpoint (verified here: Google Gemini's OpenAI-compatible API) |
| OCR | Optional: **Google Cloud Vision** (`files:annotate`, DOCUMENT_TEXT_DETECTION) or any generic HTTP OCR endpoint |
| Authentication | In-repo: PBKDF2-HMAC-SHA256 (stdlib `hashlib`), opaque sessions stored as SHA-256 digests, HttpOnly cookie |
| PDF | ReportLab (Platypus), server-rendered with an embedded DejaVu Sans for Unicode |
| Historical sources | MediaWiki search/page-images + Wikidata (public, key-free) |
| Optional synthesis | Adaption Labs (grounded-summary only; inert for this key) |
| Graph rendering | Cytoscape.js (client) |
| Tooling | npm scripts at the repository root (`scripts/`), no Docker, no CI config |

---

## 4. Prerequisites

| Requirement | Version | Needed for |
|---|---|---|
| Node.js | ≥ 20.19 (22.12+ recommended; verified on 24.21) | frontend, root scripts |
| Python | 3.11+ (verified 3.11.9) | backend |
| npm | ships with Node | dependency install |
| Any modern browser | Chrome/Edge/Firefox/Brave — verified on Brave 156 and Chrome 156 | using the app |
| PostgreSQL server | optional | production-style database (SQLite is enough locally) |

Optional accounts/keys: a **vision** provider (required for real image analysis),
a **Google Cloud Vision** (or generic HTTP) OCR credential (optional), an
**Adaption** key (optional, synthesis only). Everything else — accounts, search,
retrieval, photographs, related topics, history, PDF export — runs locally;
historical records come from public key-free APIs. Without a vision key the app
still runs: analysis answers 503 with the exact variables to set, or you can
start the labelled fixture pipeline with `npm run dev -- --demo`.

---

## 5. Quick start

From a fresh clone:

```bash
# 1. Enter the repository
git clone <your-repository-url> heritagegraph-ai
cd heritagegraph-ai

# 2. One-time setup: backend venv + both dependency sets, backend/.env,
#    database migrations. Safe to re-run; it never deletes or overwrites.
npm run setup

# 3. Start the whole application (backend + frontend) with one command
npm run dev
```

`npm run dev` prints a configuration summary first, then the URLs:

```
HeritageGraph AI is running

  App          http://127.0.0.1:5173
  Backend API  http://127.0.0.1:8000
  API docs     http://127.0.0.1:8000/docs
  Self-check   http://127.0.0.1:8000/api/diagnostics
  Mode         LIVE (real providers)
  Logs         .dev-logs/backend.log, .dev-logs/frontend.log

  Press Ctrl+C to stop both services.
```

Open the **App** URL in any browser. Useful variants:

```bash
npm run dev -- --demo          # labelled fixture pipeline (no credentials needed)
npm run dev -- --backend-port 8125 --frontend-port 5180
npm run dev -- --no-frontend   # backend only (API work)
npm run test                   # backend + frontend suites
npm run build                  # production frontend build
```

The runner detects occupied ports, reports them, and moves to the next free one;
it verifies `/api/health` and the frontend before printing the banner; if a
service dies it stops the other one and shows that service's last log lines; and
Ctrl+C (or any exit) kills both process trees.

---

## 6. Environment configuration

Where values live, in precedence order for the backend:

1. the real process environment (an explicit export always wins);
2. `backend/.env` — **non-secret** local values (created by `npm run setup`
   from [backend/.env.example](backend/.env.example));
3. `backend/.vision_key` / `backend/.adaption_key` — one-line secrets, git-ignored;
4. built-in development defaults (`DATABASE_URL=sqlite:///heritage.db`,
   `HG_LIVE_SOURCES=1`).

The repository root [.env.example](.env.example) is the full reference (it is
documentation, nothing loads it automatically). **Never commit credentials**;
keep them in the key files, your shell, or your deployment's secret store.

### Backend core

| Variable | Required | Purpose / notes |
|---|---|---|
| `DATABASE_URL` | **yes** | SQLAlchemy URL, e.g. `sqlite:///heritage.db` or `postgresql://user:pass@host:5432/heritage` |
| `ENVIRONMENT` | no | `development` (default) · `test` · `production` (JSON logs, demo mode refused) |
| `API_PREFIX` | no | default `/api` |
| `PORT` / `HOST` | no | informational for the server (`npm run dev` picks real ports); `PORT=0` is sanitised away |
| `LOG_LEVEL`, `LOG_JSON`, `APP_NAME` | no | default `INFO`, JSON on in production |

### Image analysis (vision + OCR)

| Variable | Required | Purpose / notes |
|---|---|---|
| `VISION_API_BASE_URL` | **for live analysis** | any OpenAI-compatible base, e.g. `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `VISION_API_KEY` | **for live analysis** | put it in `backend/.vision_key` locally; never logged |
| `VISION_MODEL` | **for live analysis** | e.g. `gemini-3.5-flash-lite` (list `GET {base}/models` for what your key can use) |
| `VISION_TIMEOUT` | no | default 120 s (full-resolution heritage photos measured 60–90 s) |
| `OCR_PROVIDER` | no | `google_vision` (preferred) or `http` (generic multipart endpoint); unset → `http` |
| `GOOGLE_APPLICATION_CREDENTIALS` | for Google Vision | path to a service-account JSON key; the adapter mints an OAuth2 token itself. Prefer workload/managed identity in production |
| `GOOGLE_VISION_ACCESS_TOKEN` | alternative | short-lived OAuth2 token, e.g. `gcloud auth print-access-token` in development |
| `GOOGLE_VISION_API_KEY` | alternative | Vision API key, if that is how the project is provisioned |
| `GOOGLE_CLOUD_PROJECT` | no | quota project for user-credential flows (not needed for a service account) |
| `GOOGLE_VISION_ENDPOINT` | no | default `https://vision.googleapis.com/v1/images:annotate` |
| `OCR_API_URL`, `OCR_API_KEY` | for `http` OCR | **set both or neither** — half-configured is reported as an error. Absent → vision-only with an explicit `OCR skipped` warning |
| `OCR_TIMEOUT` | no | default 30 s (both OCR providers) |

Verify credentials before use (one real image through the production adapters):

```bash
cd backend && .venv/Scripts/python -m scripts.vision_smoke ../path/to/monument.jpg
# exit 0 = provider answered and output validated · 2 = configuration · 3 = provider call failed
```

### Historical sources, media proxy and synthesis

| Variable | Required | Purpose / notes |
|---|---|---|
| `HG_LIVE_SOURCES` | no | `1` queries Wikipedia/Wikidata (public, key-free) for records, photographs and topic metadata; off → local seeded store only |
| `HG_SOURCE_TIMEOUT` | no | default 8 s |
| `HG_MEDIA_PROXY` | no | `off` loads upstream image URLs directly instead of through `GET /api/media` |
| `HG_MEDIA_USER_AGENT` | recommended | Wikimedia's policy requires a descriptive agent with a contact URL/email — **its CDN answers 403 without one**; default uses `APP_ORIGIN`/localhost |
| `APP_ORIGIN` | no | this deployment's public origin; used as the proxy's default contact |
| `HG_SYNTHESIS` | no | `1` lets the synthesis provider compose one paragraph over the briefing's quoted statements (grounding-checked); off → verbatim quotes only |
| `ADAPTION_API_KEY`, `ADAPTION_MODEL`, `ADAPTION_API_BASE_URL`, `ADAPTION_TIMEOUT` | no | optional synthesis provider; key can live in `backend/.adaption_key` |
| `HG_DEMO_MODE` | no | `1` labelled fixture pipeline; ignored when `ENVIRONMENT=production` |

### Authentication and sessions

| Variable | Required | Purpose / notes |
|---|---|---|
| `SESSION_TTL_HOURS` | no | session lifetime in hours, default `168` (7 days), clamped to 1 h … 30 d |
| `TRUST_PROXY_HEADERS` | no | `1` only when a reverse proxy **sets** `X-Forwarded-Proto`; otherwise a client could claim `https` and receive a `Secure` cookie over plain HTTP. Off by default |
| `CORS_ORIGINS` | no | comma-separated allowed origins, for a deployment that serves the frontend from a different origin. `*` is refused (credentialed requests cannot use a wildcard). Same-origin deployments need nothing |

No signing secret is required: the client only ever holds an opaque 256-bit
token, stored server-side as a SHA-256 digest, so there is nothing to verify
with a key. Sessions are revoked on logout and rejected after expiry.

### Frontend and dev server

| Variable | Required | Purpose / notes |
|---|---|---|
| `VITE_API_BASE_URL` | no | API origin for standalone builds/tests. Empty (default) → same-origin `/api`, which is the design: one origin, no CORS. A malformed value is reported in the console and ignored |
| `VITE_MEDIA_PROXY` | no | `off` disables the client-side `/api/media` rewriting (images load from upstream) |
| `HG_BACKEND_ORIGIN` | no | what the dev server proxies `/api` to; set automatically by `npm run dev` |
| `VITE_DEV_HOST` | no | bind address (default `127.0.0.1`); `0.0.0.0` to open the app from another device. Accounts work over plain HTTP on a LAN, but `Secure` cookies are not set there — for anything beyond local development terminate TLS at a proxy |
| `VITE_DEV_PORT`, `VITE_PREVIEW_PORT` | no | 5173 / 4173 |
| `VITE_ALLOWED_HOSTS` | no | extra `Host` values the dev server accepts, comma separated, or `*`. Without it Vite answers **403 “Blocked request. This host is not allowed.”** for tunnels/machine names |

---

## 7. Running and using the application

### Start

`npm run dev` (see §5). Ports: frontend 5173, backend 8000 by default; both move
to the next free port when busy and the real URLs are printed.

Access model: **text search is public**; **image analysis, the analysis status
page, history and PDF export require a signed-in account**, because an analysis
is somebody's private record. Requests without a session get `401`, and
another user's analysis id answers `404` (not `403`), so ids cannot be probed.

Health and self-check:

```bash
curl http://127.0.0.1:8000/api/health        # liveness  → {"status":"ok", ...}
curl http://127.0.0.1:8000/api/ready         # readiness → 200 ready / 503 with per-check detail
curl http://127.0.0.1:8000/api/diagnostics   # configuration + provider status (no secrets)
curl "http://127.0.0.1:8000/api/diagnostics?probe=1"   # + one cheap authenticated provider call
```

### Using it

0. **Create an account** — the header shows *Sign in*. Use the **Create
   account** tab with an email and a password (min. 8 characters); you are
   signed in immediately and the session survives a page refresh. Sign in, sign
   out and the session probe all go through `/api/auth/*`; protected views tell
   you when the session expired instead of silently showing an empty page.
1. **Search** — type a person, monument, place, event or year (e.g. `Rani ki
   Vav`, `Qutub Minar`, `1946`) and submit. Invalid input is rejected in the
   browser before any request. A signed-in search is recorded in your history.
2. **Read the subject overview** — name, type, description, its photograph with
   author/license/file link, the quoted explanation with `[n]` citations, key
   facts and dated events, and the source list. Switch tabs for **Related
   topics**, **Knowledge graph** and **Sources**.
3. **Inspect the text-search graph** — the graph tab shows the subject, the
   records retrieval actually returned, and any `mentions` edge supported by a
   record's own text. Selecting a node shows its type and description; selecting
   an edge shows the sentence that supports it. With no records the tab says
   that no graph could be built rather than drawing a decorative one.
4. **Follow a related topic** — click any topic card: its own overview,
   photograph, sources, graph and topics replace the current one, and the URL
   becomes `/?q=<topic>` (Back returns to the previous subject).
5. **Upload an image** — choose a JPEG/PNG/WebP/GIF/TIFF/AVIF up to 10 MiB; a
   local preview appears, then *Submit for analysis*.
6. **Watch the analysis** — the panel shows `Queued` → `Processing (n %)` →
   `Completed`, `Completed` with warnings, `Partial`, or `Failed` with a safe
   message and the reason. A run is never marked completed because the upload
   was accepted.
7. **Read the result** — extracted text (explicitly “no text could be
   extracted” when there was none), the AI reading labelled as a *candidate*,
   the candidate subject with its photograph, the historical context with
   citations, related topics, detected entities, matched records.
8. **Inspect evidence** — click an edge in the relationship graph to see the
   records evaluated for it and the verification status.
9. **Reopen anything** — the **History** view lists this account's searches and
   analyses, newest first, with type, status, subject and summary. Selecting one
   restores the stored result (briefing, sources, graph, limitations) without
   re-running the AI request. **Export PDF** downloads that record; **Export all**
   downloads the whole history.

### Browsers

Any modern browser works: the frontend only ever calls `/api` on its own origin
(the Vite dev server proxies it; a production deployment reverse-proxies it), so
there is no CORS step and no hardcoded `localhost` that would point at the
visitor's own machine. Subject photographs and thumbnails are additionally
served through the app's own origin (`/api/media`), so a browser that cannot
reach the image host — shields, DNS filtering, VPN, corporate proxy — still shows
them; a deployment without the proxy falls back to the upstream URL.

Verify a browser end to end (real browser, real app, no mocks):

```bash
npm run check:browser -- --url http://127.0.0.1:5173 --search "Qutub Minar"
npm run check:browser -- --image backend/testdata/eval/t1.jpg --image backend/testdata/eval/t5.jpg
npm run check:browser -- --browser "C:/Program Files/Google/Chrome/Application/chrome.exe"   # another engine
# simulate a browser that cannot reach the image host at all (shields, DNS, VPN):
npm run check:browser -- --browser "<brave.exe>" \
  --arg "--host-resolver-rules=MAP upload.wikimedia.org ~NOTFOUND, MAP thumb.wikimedia.org ~NOTFOUND"
```

It drives Brave (or Chrome/Edge) over the DevTools protocol: loads the app, runs
a text search, checks that the subject photograph really decoded, compares the
proxied image against a direct upstream load, opens a related topic, uploads each
image through the real file input, waits for a terminal analysis status, and
reports the page's console errors, failed requests and non-2xx responses. Exits
non-zero on failure, and can write a JSON report (`--json path`).

---

## 8. Testing and production build

### Commands (all verified)

```bash
npm test                     # backend pytest + frontend vitest, with a summary
npm test -- --backend-only   # pytest only      (extra args after -- are forwarded)
npm test -- --frontend-only  # vitest only
npm run typecheck            # tsc --noEmit
npm run build                # production frontend build (typecheck + vite build)

# Individual suites
cd backend  && .venv/Scripts/python -m pytest -q
cd backend  && .venv/Scripts/python -m pytest -m integration       # live-HTTP layer
cd frontend && npm test
cd frontend && VITE_API_BASE_URL=http://127.0.0.1:8000 npm run test:integration   # needs a running backend
```

There is **no lint configuration and no coverage measurement** in this
repository — no lint/coverage result is claimed. The frontend build prints a
chunk-size advisory for the 709 kB bundle; it is not an error.

### Results from this pass

| Command | Result |
|---|---|
| `npm run setup` | venv + deps verified, migrations applied to head `0005_users_sessions_history`; with `backend/.env` moved aside it recreated the file from `backend/.env.example` (non-secret keys only), and the original was restored byte-identical |
| `npm run setup -- --seed` | migrations to head + curated seed loaded (idempotent: 0 new records) |
| `cd backend && pytest -q` | **529 passed, 5 skipped** (100.3 s), including the R7 suites: accounts, sessions, authorization, history, text-search graph, PDF export |
| `cd frontend && npm test` | **92 passed, 2 skipped** (12 files) — includes AuthPanel, HistoryPage, SearchGraphPanel and the partial-status contract test |
| `npm run typecheck` | clean (`tsc --noEmit`, tests included) |
| `npm run build` | succeeded (`dist/` 739 kB / 228 kB gzip; chunk-size advisory only) |
| `npm run dev` | both services started, `/api/health` 200, banner printed with the real URLs; search answered through the same-origin proxy; killing one service stopped the other and left **no orphan processes** (ports freed) |
| Live HTTP run against a real uvicorn server + real vision provider (scripted `httpx` client, not a browser) | signup `201` with an `HttpOnly; SameSite=Lax` session cookie · `GET /api/search/graph` returned the subject + 14 record nodes and co-mention edges from real Wikipedia records · a real monument photo reached the configured vision model and the analysis finished `completed` with 2 entities and 8 matched records (with the honest `OCR skipped` warning, since no OCR is configured here) · `GET /api/history` listed both the search and the analysis with their terminal statuses · one-record and full-history PDFs downloaded as valid `%PDF` documents (50 kB / 56 kB) · a second account got `404` for the first account's analysis, history record and export, and an empty history list · logout `204` and the old cookie then answered `401` |
| `npm run check:browser` (Chrome 156) | PASS **7/7** — sign in (account created on first run), search, subject photo + proxy comparison, related-topic navigation, upload t1 (real vision run → `Completed`, 1–3 entities, 5–8 matched records) and t5 (→ `Partial`, 0 entities, warnings shown, **not** a fake success), 0 console errors, 0 responses ≥ 400 |
| `npm run check:browser -- --browser <brave.exe>` (Brave 156) | PASS **6/6** — same flow with one upload (`Completed`, 3 entities, 8 matches), 0 console errors, 0 responses ≥ 400 |
| Brave with the image hosts unreachable (`~NOTFOUND`) | earlier pass, before accounts existed: the subject photograph still rendered through `/api/media` (`renderedDecoded: true`) **while the identical direct upstream load failed** (`net::ERR_NAME_NOT_RESOLVED`); the proxy has not changed since, but this scenario was not re-run against the authenticated build |

### Mocked vs real

| Layer | Real or mocked |
|---|---|
| Backend unit/contract tests | in-process `TestClient`, mocked provider transports |
| Backend database tests | **real** migrated SQLite through the Alembic pipeline and repositories |
| Backend HTTP integration layer | **real** uvicorn process over loopback (`-m integration`) |
| Provider (vision) tests | mocked by default; `HERITAGE_LIVE_PROVIDER_TESTS=1` makes **real** calls |
| OCR tests | **all mocked** (`httpx.MockTransport` over real Cloud Vision annotate payloads); no live Google Cloud Vision call was possible without GCP credentials — see [§9](#9-troubleshooting) |
| Auth / history / PDF tests | **real** PBKDF2 hashing, real session rows, real migrated SQLite, real ReportLab bytes parsed back out of the response |
| Historical source tests | mocked by default; `HERITAGE_LIVE_SOURCE_TESTS=1` makes **real** MediaWiki calls |
| Frontend tests | jsdom with a mocked `fetch` boundary |
| Browser checks (`npm run check:browser`) | **real browser, real app, real network** (provider calls included) |

---

## 9. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `npm run dev` says the venv or node_modules is missing | Run `npm run setup` once, then `npm run dev` again |
| `ConfigurationError: PORT must be between 1 and 65535 (got 0)` | Some dev shells export `PORT=0`. `npm run dev` sanitises it; if you start the server by hand, unset `PORT` first |
| Port already in use | `npm run dev` detects it, reports it and moves to the next free port (the printed URL is the real one). For manual starts: `netstat -ano \| grep 8000` and pass `--port`/`--backend-port` |
| `POST /api/analysis` → 503 naming `VISION_*` | No vision credentials. Set `VISION_API_BASE_URL`, `VISION_API_KEY` (e.g. in `backend/.vision_key`) and `VISION_MODEL`, or run `npm run dev -- --demo` for labelled fixtures |
| Analysis reaches `Failed` | Read the message in the status panel (it is sanitised) and the backend log in `.dev-logs/backend.log`; common causes are provider auth/quota, a model id your key cannot use, or an unreachable provider |
| Analysis stuck on `Processing` | The frontend polls and gives up after ~3 minutes; the backend records the failure on the run. Check `.dev-logs/backend.log` for the run id |
| “OCR skipped: OCR service is not configured” | No OCR provider is configured. Set `OCR_PROVIDER=google_vision` plus one Google credential (`GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_VISION_ACCESS_TOKEN` or `GOOGLE_VISION_API_KEY`), or `OCR_PROVIDER=http` plus `OCR_API_URL`/`OCR_API_KEY`. Until then text comes from the vision model only and the run says so instead of inventing an inscription |
| “Google Cloud Vision is not configured …” | The provider is selected but no credential was found. The message names the variables; nothing is sent without a credential |
| OCR 403 / `PERMISSION_DENIED` | The credential is valid but the Vision API is not enabled for that project, or the service account lacks the Vision role. Enable `vision.googleapis.com` and grant `roles/cloudvision.user` (the adapter never retries a permission error as if it were transient) |
| Extracted text is empty on a document image | The vision model returned no legible text and no OCR is configured; OCR is the reliable path for inscriptions and printed documents — configure it (above). The run is then reported `partial` (useful visual result, no text) rather than `completed`, and the app says “No text could be extracted” instead of showing a fabricated certificate |
| Analysis says `Partial` | A non-critical stage failed or found nothing (e.g. no text extracted, retrieval returned no records) while the rest succeeded. Read the warnings on the result; re-run with OCR configured if the text mattered |
| History is empty after searching | History is per account and text search is recorded only when signed in. Sign in first; a signed-out search is not stored (and the History view says so) |
| `401` on upload or history | No/expired session cookie. Sign in again; cookies must be enabled for the app origin. `SESSION_TTL_HOURS` controls the lifetime |
| `404` for an analysis you just created | The analysis belongs to another session/account (or was created before you signed in). Ids are owner-scoped deliberately; there is no way to read someone else's run |
| Login says “Invalid email or password” | Deliberate: the endpoint does not reveal whether the address exists (no account enumeration). Create an account if this one was never registered |
| Signup `429` | The credential endpoints are rate-limited per client. Wait for the window to pass (the response says how long) |
| PDF export fails with `503` | ReportLab could not render (rare). The endpoint returns the standard error envelope with a request id; check `.dev-logs/backend.log` for that id |
| PDF shows boxes for a script outside the embedded font | DejaVu Sans covers Latin, Greek, Cyrillic and common punctuation. Glyphs outside its coverage (e.g. Devanagari) are a known limitation of the current export; the rest of the document still renders |
| **Brave (or another browser) shows no subject photographs** while text search works | The browser could not load the third-party image host. The proxy (`GET /api/media`) fixes this; verify with `curl "http://127.0.0.1:8000/api/media?url=https%3A%2F%2Fthumb.wikimedia.org%2F..."` → 200, and `GET /api/diagnostics` → `media_proxy.enabled: true`. If you disabled it (`HG_MEDIA_PROXY=off`), images load directly and depend on the browser reaching Wikimedia |
| Images 403 from the proxy | Wikimedia's CDN refuses agents without contact information. Set `HG_MEDIA_USER_AGENT` to your project URL/email (verified: the same request then succeeds). `api/diagnostics` reports `media_proxy.user_agent: default|configured` |
| `403 Blocked request. This host is not allowed.` | The dev server rejected an unknown `Host` (tunnel, machine name). Set `VITE_ALLOWED_HOSTS=that-host` (or `*`, development only) |
| Opening the app on `http://<LAN-IP>:5173` does nothing | The dev server binds `127.0.0.1` by default. Set `VITE_DEV_HOST=0.0.0.0` and restart; note that Brave's HTTPS-Only mode may upgrade a typed `http://` LAN URL — open the exact `http://` URL and allow it for that host |
| “Search failed / Could not reach the server” | The page could not reach `/api` on its own origin: the backend is not running (check `.dev-logs/backend.log`) or a reverse proxy is not forwarding `/api`. `GET /api/diagnostics` from the same origin tells you whether the backend is healthy |
| `/api/ready` → 503 | Read `checks`: `configuration` names missing variables, `database` means `DATABASE_URL` is unset/unreachable (details are in the server log) |
| Search returns nothing | With `HG_LIVE_SOURCES` off only the seeded records exist; run `npm run setup -- --seed` (or `python -m scripts.run_server --migrate --seed`). With it on, a Wikimedia `429`/outage produces a `notice` in the UI rather than a normal-looking empty archive |
| Results are labelled **DEMO MODE** | The run used the fixture pipeline: `--demo` was passed or `HG_DEMO_MODE=1` is set. Remove it for live analysis |
| CORS errors | Not expected: the app calls `/api` on its own origin (dev proxy or reverse proxy). If you point `VITE_API_BASE_URL` at another origin, that deployment needs explicit CORS configuration — the backend has none by design |
| Database file looks stale/corrupt | Stop the servers, restore or delete the SQLite file (development only), then `python -m scripts.run_server --migrate --seed` |

Diagnostic one-liners:

```bash
curl -s http://127.0.0.1:8000/api/diagnostics | python -m json.tool
curl -s http://127.0.0.1:8000/api/ready | python -m json.tool
tail -n 40 .dev-logs/backend.log
```

---

## 10. Security notes

* **Credentials** live in `backend/.vision_key`, `backend/.adaption_key`, the
  shell, or the deployment's secret store — never in a committed file, never in
  frontend code, never in a URL. `.gitignore` excludes `.env*` (except the
  templates), the key files, logs and `node_modules`.
* Only `VITE_*` values reach the browser bundle; the API keys are backend-only
  and are never sent to the client or echoed by any endpoint (`/api/diagnostics`
  reports presence, host name and model — not values).
* **Uploads** are validated server-side before decoding: 10 MiB cap (413),
  MIME/extension allow-list (415), magic-byte sniffing, pixel/dimension and
  decompression-bomb limits, empty-file rejection.
* **The media proxy is not an open proxy**: `https` only, fixed host allowlist
  (`upload.wikimedia.org`, `thumb.wikimedia.org`), every redirect hop
  re-validated, image content types only, 8 MiB cap, no cookies or authorization
  headers forwarded, responses marked `nosniff`.
* **Passwords** are hashed with PBKDF2-HMAC-SHA256 (600 000 iterations,
  16-byte per-user salt) and compared in constant time; a dummy hash keeps
  unknown-email logins timing-similar, and login answers one generic message so
  the endpoint cannot be used to enumerate accounts. Plaintext is never stored,
  logged or returned — the user object carries no hash field at all.
* **Sessions** are 256-bit opaque tokens (`secrets.token_urlsafe(32)`) stored
  only as SHA-256 digests, so a database dump cannot be replayed as cookies.
  The cookie is `HttpOnly`, `SameSite=Lax`, `Path=/`, and `Secure` whenever the
  request is HTTPS (`TRUST_PROXY_HEADERS` gates trusting `X-Forwarded-Proto`).
  Logout deletes the row; expiry (`SESSION_TTL_HOURS`) is checked per request.
* **CSRF**: state-changing endpoints (upload, logout, export) reject
  cross-origin writes, and `SameSite=Lax` blocks the cookie on cross-site POSTs;
  no cookie-authenticated write is exposed to a plain cross-site form.
* **Authorization** is enforced in SQL, not in the browser: history list/detail/
  export, analysis status, analysis graph and evidence all filter on the session
  user's id. Client-supplied ids are never trusted, and another user's record is
  reported as `404` so ids cannot be probed. Verified by cross-user tests and by
  the live two-account run in §8.
* **Rate limiting** applies to signup/login, uploads and exports (per client and
  per user), returning `429` with `Retry-After`.
* **CORS** is off by default (one origin, proxied); `CORS_ORIGINS` enables an
  explicit allow-list and refuses `*` with credentials.
* **Errors** use one envelope for every non-2xx response; stack traces, SQL and
  upstream URLs never reach the client; a server-generated `X-Request-Id`
  correlates a user-visible error with the log line.
* **PDF exports** are generated per request from owner-scoped rows, streamed
  with `Content-Disposition: attachment` and a sanitised filename, and marked
  `Cache-Control: private, no-store` — no public or guessable download URL and
  no internal path in the response.
* **Not present**: TLS (terminate at a reverse proxy), email verification /
  password reset (no mail provider), and any deployed environment.

---

## 11. Project structure

```
.
├── package.json              # one-command scripts: setup / dev / test / build
├── scripts/
│   ├── setup.mjs             # first-time setup (venv, deps, .env, migrations)
│   ├── dev.mjs               # starts + supervises backend and frontend
│   ├── test.mjs              # both suites in one command
│   ├── runtime.mjs           # shared helpers (ports, venv, process trees)
│   └── browser-check.mjs     # real-browser end-to-end check (CDP, no deps)
├── backend/
│   ├── app/
│   │   ├── api/routes/       # health, ready, diagnostics, analysis, search, search-graph, history, auth, graph, evidence, media
│   │   ├── core/             # config, error envelope, logging, security (sessions, auth guards, rate limits)
│   │   ├── db/               # session, models, migrations entry point (init_db)
│   │   ├── integrations/     # vision, ocr (+ google_vision), wikipedia, wikimedia_media, wikidata, adaption
│   │   ├── repositories/     # records, entities, analyses, relationships, evidence, users, sessions, history
│   │   ├── schemas/          # the canonical API contract (Pydantic)
│   │   └── services/         # analysis orchestration, retrieval, briefing, extraction, evidence, image,
│   │                         # auth, history, search_graph, export, pdf
│   ├── scripts/              # run_server.py (the supported start path), vision_smoke, probes, eval harness
│   ├── tests/                # unit + database + live-HTTP suites (39 files, incl. auth/history/pdf/graph)
│   └── .env.example          # non-secret local defaults (copied to backend/.env by setup)
├── database/
│   ├── alembic.ini
│   ├── migrations/versions/  # 0001 … 0005 schema chain (0005 = users, sessions, ownership, history)
│   └── seeds/                # curated development records
├── docs/
│   └── HeritageGraph-AI.pdf  # project documentation / report
├── frontend/
│   ├── src/app/              # React entry point, view routing (Explore / Analyze / History)
│   ├── src/components/       # upload, analysis, briefing, subject, graph, evidence, search-graph,
│   │                         # auth panel, layout
│   ├── src/hooks/            # analysis submission, auth state, subject route, image fallback
│   ├── src/pages/            # HomePage (search + subject + graph tabs), AnalysisPage, HistoryPage
│   ├── src/services/         # api.ts (transport, normalisers), accountApi.ts (auth/history/export)
│   ├── src/types/api.ts      # mirrored contract types
│   └── tests/                # Vitest component/unit suites
├── screenshots/              # curated UI evidence (PNG/WebM); machine reports are git-ignored
├── .env.example              # full environment reference (documentation)
├── REPAIR_PLAN.md            # repair history and the reasoning behind earlier fixes
└── README.md                 # this guide
```

Local-only state is deliberately outside version control: `.dev-logs/` (runner
logs and archived probe artifacts), `.sidecar/`, `.freebuff/`, `backend/.venv`,
`node_modules`, `frontend/dist`, `backend/heritage.db` and the secret files
`backend/.vision_key`, `backend/.adaption_key`, `backend/.env`.

Where to look for what: **image-analysis logic** →
`backend/app/services/analysis_service.py` (orchestration) and
`frontend/src/pages/AnalysisPage.tsx`; **provider integrations** →
`backend/app/integrations/`; **retrieval & briefings** →
`backend/app/services/retrieval_service.py`, `briefing_service.py`;
**graph/evidence** → `relationship_service.py`, `evidence_service.py` and the
`graph`/`evidence` routes; **contract** → `backend/app/schemas/` mirrored by
`frontend/src/types/api.ts`.

---

## 12. Engineering notes

### Keeping this document honest

Update this README whenever the start commands, environment variables, provider
integrations, endpoints or test commands change, and re-run the commands you
document. Results in §8 carry the date of the pass that produced them; anything
not verified is marked as such rather than implied.

### API surface

| Endpoint | Purpose |
|---|---|
| `GET /api/search?query=` | Historical search + grounded briefing (public; a signed-in search is also recorded) |
| `GET /api/search/graph?query=` | The same retrieval as a knowledge graph (nodes, edges, evidence, limitations) |
| `POST /api/auth/signup` · `/api/auth/login` | Create an account · sign in → user object, `Set-Cookie` session |
| `POST /api/auth/logout` · `GET /api/auth/me` | Revoke the session (`204`) · current user or `401` |
| `GET /api/history` | This account's history, newest first, paginated (`limit`, `offset`, `kind`) |
| `GET /api/history/{id}` | Reopen one record: stored result, graph, sources, status — never re-runs the AI |
| `GET /api/history/{id}/pdf` · `GET /api/history/export/pdf` | One record · the whole history, as an owner-scoped PDF |
| `POST /api/analysis` | Upload an image (`multipart/form-data`, field `file`) → `202 {analysisId, status}` (auth required) |
| `GET /api/analysis/{id}` | Status + extracted text, interpretation, entities, matches, briefing, warnings (owner only) |
| `GET /api/analysis/{id}/graph` | Nodes + edges (with verification status) (owner only) |
| `GET /api/analysis/{id}/relationships/{relId}/evidence` | The records evaluated for one edge |
| `GET /api/media?url=` | Same-origin proxy for allowlisted Wikimedia images |
| `GET /api/health` · `GET /api/ready` | Liveness · readiness (named checks) |
| `GET /api/diagnostics` | Safe configuration/provider self-check (`?probe=1` for one cheap provider call) |

The canonical schemas live in `backend/app/schemas/`; documented contract for
every response is generated at `/docs` and `/openapi.json`.

### Defects the final verification pass caught (and fixed)

Three problems were found by *running* the app rather than by reviewing it:

1. **Every authenticated upload was refused with `403` in development.** The
   Vite dev proxy rewrote `Host` to the backend's own port, while the browser's
   `Origin` kept the app's port, so the CSRF same-origin check compared two
   different authorities. Fixed in both places: the proxy now preserves `Host`,
   and the check also accepts the deployment's declared public origin
   (`APP_ORIGIN` / `CORS_ORIGINS`), which is what a host-rewriting reverse proxy
   needs. A test covers the proxied case while the undeclared-origin `403` stays.
2. **A `partial` analysis rendered as “Could not load the analysis.”** The
   response normaliser only listed `queued/processing/completed/failed`, so the
   newer terminal state was rejected as unrecognised even though the components
   handled it. Fixed in `frontend/src/services/api.ts` and covered by a test.
3. **The backend suite reached the live Wikipedia API.** `scripts/run_server`'s
   `main()` deliberately writes the resolved start-up environment into
   `os.environ`; the in-process tests that call it therefore leaked
   `HG_LIVE_SOURCES=1` into every later test, making results network-dependent
   (and one graph test fail intermittently). The fixture now snapshots and
   restores the environment, and the suite is deterministic and ~15 % faster.

The browser check itself also gained a sign-in step (uploads are owner-scoped)
and now accepts `Partial` as a terminal state — it used to wait forever on a
run that had legitimately finished.

### Cross-module work

Cross-module issues and their owners are tracked in
[backend/INTEGRATION_ISSUES.md](backend/INTEGRATION_ISSUES.md); repair history
and the reasoning behind earlier fixes are in [REPAIR_PLAN.md](REPAIR_PLAN.md).

### License

No `LICENSE` file exists in this repository, so no open-source permissions have
been established yet — the project owner should choose and add one.
