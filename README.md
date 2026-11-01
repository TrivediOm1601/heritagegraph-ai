# HeritageGraph AI

**From Forgotten Archives to Living Cultural Knowledge.**

**Blithchron '27 · IIT Gandhinagar · Problem statement HC26-CULT01** —
*Digital Revitalisation of Unstructured Cultural & Architectural Heritage
Archives* (Software — Open Innovation; Culture, Media & Entertainment).

**Status:** hackathon build, deployed on Render from the included `Dockerfile`.
**Live demo:** https://heritagegraph-ai.onrender.com (free hosting: the first load
after idle can take about a minute, and accounts or uploads reset on restart).
A one-page summary is in [APPROACH_AND_LIMITS.md](APPROACH_AND_LIMITS.md).

[Submission checklist](SUBMISSION_CHECKLIST.md) ·
[Repository audit](docs/repository-audit.md) ·
[Technical approach](docs/technical-approach.md) ·
[Known limitations](docs/known-limitations.md) ·
[Database schema](docs/database-schema.md) ·
[Deployment preparation](docs/deployment.md) ·
[Security](SECURITY.md) ·
[Contributing](CONTRIBUTING.md)

HeritageGraph AI turns a historical image — or a text query — into evidence-backed
history: what the image shows, what it might be, the historical records that
support (or fail to support) that reading, the entities and relationships
involved, and an interactive knowledge graph where every edge carries an
evidence status.

It now combines six connected systems:

| System | Input | Output |
|---|---|---|
| **Historical text search** | a person, place, monument, event or year | subject overview with photograph, key facts, related topics, cited sources and a knowledge graph |
| **Historical image analysis** | a photograph, scan, inscription or document | visual reading, extracted text, candidate identification, grounded history, entities, relationships, graph, evidence |
| **Heritage Archive Studio** | several images, PDF, DOCX, PPTX, TXT, Markdown or a ZIP | one archive whose files are digitised independently, with per-file status, page-level extracted text, entities, a dated timeline, duplicate detection, search and a PDF report |
| **India Heritage Explorer** | nothing (curated dataset) | 67 India-wide heritage sites covering all 28 states and 8 Union Territories, with sourced coordinates, national filters, a searchable site list and a clearly-labelled approximate route |
| **Stories & Legends** | a curated heritage site | 75 narratives across all 67 sites (the first 18 hand-checked, 57 short summaries marked unverified) — history, mythology, local legend, architecture, culture — each labelled by its epistemic basis, with separate versions when traditions disagree, a source panel, retrieval-only Q&A, and a reviewed community oral-history intake |
| **3D depth view** | a photograph chosen in the upload box | a tilt-and-zoom depth relief of the photo; depth is AI-estimated in the browser (Depth Anything V2) and labelled as an estimate, with an approximate fallback if the model cannot load |
| **Research workspace + account** | archives, notes, email + password | collections of archives with notes, a combined PDF report, and private owner-scoped history of every search and analysis |

Everything the app answers with is traceable: explanations quote the retrieved
sources sentence by sentence with citation numbers, and a claim with no
retrieved source is reported as such instead of being filled in.

### Screenshots (real build, 31 Oct 2026)

![Home — historical search and image upload](docs/screenshots/home.png)

*Home: the two entry points — historical search and image upload.*

![Subject overview for Rani ki Vav](docs/screenshots/subject-overview-detail.png)

*Subject overview with the sourced photograph, attribution, quoted history and
citation numbers.*

![India Heritage Explorer](docs/screenshots/india-heritage-map.png)

*India Heritage Explorer: 67 curated sites, all 36 states/UTs in the filter,
markers drawn only where a published coordinate exists.*

![Stories & Legends — Konark alternative traditions](docs/screenshots/stories-legends.png)

*Stories & Legends: the Konark origin story keeps two conflicting traditions
side by side, each with its own sources.*

More: [archive studio](docs/screenshots/archive-studio.png) ·
[research workspace](docs/screenshots/research-workspace.png) ·
[subject search](docs/screenshots/subject-overview.png) ·
[map + national summary](docs/screenshots/india-heritage-explorer.png).

### Documentation map

| Document | What it answers |
|---|---|
| [README](README.md) | identity, features, setup, API, tests, troubleshooting |
| [docs/repository-audit.md](docs/repository-audit.md) | what was inspected, security findings, Git/publication risk |
| [docs/cleanup-report.md](docs/cleanup-report.md) | what was removed/retained and why |
| [docs/technical-approach.md](docs/technical-approach.md) | how the system works end to end |
| [docs/known-limitations.md](docs/known-limitations.md) | what is missing or unverified, with impact and next steps |
| [docs/database-schema.md](docs/database-schema.md) | tables, relationships, migrations, ownership |
| [docs/deployment.md](docs/deployment.md) | production topology and configuration (deployed on Render using the Dockerfile) |
| [SECURITY.md](SECURITY.md) | verified controls, reporting, security limits |
| [CONTRIBUTING.md](CONTRIBUTING.md) | setup, conventions, tests, PR expectations |
| [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md) | honest status of every submission item |

---

## 1. Project overview

### Problem statement

Cultural and architectural heritage documentation is produced continuously —
site photographs, scanned reports, conservation PDFs, presentation decks, travel
journals — and then lands in disconnected folders. The information is real but
unusable as knowledge: it cannot be searched by page, its people and monuments
are not linked, its dates are not on a timeline, its claims have no visible
provenance, and every new researcher starts from zero.

HeritageGraph AI addresses that directly. A user can search live historical
sources for a subject, analyse a photograph with a vision model and OCR, or
upload a whole collection and have every file digitised independently into a
searchable archive with page references, entities, a timeline, duplicates, a
curated map and an evidence-backed report. Nothing is invented: an empty OCR
result stays empty, a relationship without a supporting record is marked
unsupported, and a marker without a published coordinate is not drawn.

### System architecture

```mermaid
flowchart TD
    U[Browser] -->|same origin /api| F[React 19 + Vite]
    F --> API[FastAPI backend]
    API --> AUTH[Accounts and sessions]
    API --> SEARCH[Historical search]
    API --> VISION[Image analysis]
    API --> ARCH[Heritage Archive Studio]
    API --> MAP[India Heritage Explorer]
    API --> STORIES[Stories & Legends]
    API --> RESEARCH[Research workspace]
    SEARCH --> WIKI[Wikipedia / Wikidata<br/>public, key-free]
    VISION --> VP[Vision provider<br/>OpenAI-compatible]
    VISION --> OCR[OCR provider<br/>OCR.space or Google Vision]
    ARCH --> DOC[PDF / DOCX / PPTX / TXT / MD extraction]
    ARCH --> OCR
    ARCH --> STORE[(Original files on disk)]
    ARCH --> DB[(SQLite / PostgreSQL)]
    SEARCH --> DB
    AUTH --> DB
    RESEARCH --> DB
    MAP --> DB
    STORIES --> DB
    STORIES --> CORPUS[Curated, sourced narratives<br/>with versions and citations]
    ARCH --> REPORT[ReportLab PDF reports]
    RESEARCH --> REPORT
```

One origin: the dev server (and any deployment reverse proxy) forwards `/api`,
so the browser never makes a cross-origin request and there is no CORS step that
can fail per browser. Uploaded bytes go to the backend from the browser's own
origin; credentials stay on the server.

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

### How the Heritage Archive Studio works

`POST /api/archives` creates an owner-scoped archive; `POST
/api/archives/{id}/files` accepts up to 20 files per request (25 MB each, 100 MB
ZIP, 150 MB per request). A ZIP is inspected **before** anything is extracted:
path traversal, absolute paths, executable members, over-limit entries and
nested archives are skipped and reported as warnings, never written. Each
accepted file is stored on disk under an opaque key (its bytes never go into a
database column) and processed independently in the background, so one failure
does not stop the others.

The pipeline is: validate → store → classify → extract (text layer or OCR for
images/scan-only PDFs; page/slide numbers preserved) → normalise → detect
language → extract entities → find dated events → detect duplicates (SHA-256 for
identical files, dHash for similar photographs, text fingerprints for documents)
→ mark `completed`, `partial` or `failed` with a reason. Text is stored per page,
so search returns the exact page an excerpt came from. Search is **keyword**
search and says so: no embedding provider is configured, and it is never
labelled semantic. Archive and collection reports are generated server-side with
ReportLab and include the extracted passages, entities, timeline, limitations and
source references.

```mermaid
flowchart LR
    UP[Upload: images, PDF, DOCX,<br/>PPTX, TXT, MD or ZIP] --> V[Validate type and size]
    V --> Z{ZIP?}
    Z -->|yes| S[Safe inspection:<br/>traversal, executables,<br/>depth, limits]
    S --> C[Each accepted member]
    Z -->|no| C
    C --> ST[(Preserved original)]
    C --> K{Classify}
    K -->|image| OCR[OCR provider]
    K -->|PDF| T[Text layer + OCR<br/>for scanned pages]
    K -->|DOCX / PPTX| X[Package XML extraction]
    K -->|TXT / MD| X
    OCR --> N[Normalise + detect language]
    T --> N
    X --> N
    N --> E[Entities + dated events]
    N --> D[Duplicates: SHA-256,<br/>dHash, text fingerprint]
    E --> DB[(Database: pages, entities,<br/>timeline and status)]
    D --> DB
    DB --> Q[Keyword search + timeline]
    DB --> R[PDF report]
```

### How the India Heritage Explorer works

`GET /api/heritage/sites` serves the curated national dataset straight from the
database (`heritage_sites`, extended by migration `0007` with region, period
bucket, alternative names, district/city, architectural style, UNESCO status and
multiple source references). The seed in `database/seeds/heritage_sites.json`
holds **67 records covering all 28 states and 8 Union Territories**, 14
categories and 29 UNESCO-listed sites. Every record carries its source article(s),
a `coordinateSource`, `coordinateAccuracy` and `verificationStatus`; coordinates
are published figures (Wikipedia) **rounded to four decimals and marked
`approximate`** — no record claims field-survey precision, and the dataset notice
says so in the UI. All 67 records are curated demonstration data
(`dataset: curated-demo-india`), never mixed with user-contributed archive
material.

The API supports server-side filtering and paging: `q` (name, city, state,
district or alternative name), `state` (all states/UTs, including the eight Union
Territories), `region` (documented mapping of each state/UT to North / South /
East / West / Central / Northeast India), `category`, `periodGroup` and `unesco`,
with `sort`, `limit` and `offset`. Each response also carries the filtered total,
the national summary (sites, states and UTs represented, categories, verified
records, approximate coordinates, UNESCO sites) and complete state/UT + region +
category + period facets, so the filter lists show real counts. The map page
takes the curated dataset in one request (capped at 500 records, far above the 67
it currently has) and applies the same filters, site search and sorting in the
browser, rendering ten cards per page — so the DOM never holds more than a page
of cards and the map only receives the filtered set. Dataset and API behaviour is
covered by `backend/tests/test_heritage_india.py` and the heritage cases in
`backend/tests/test_archive_api.py`; the UI flows by
`frontend/tests/MapPage.test.tsx` and `frontend/tests/HeritageMap.test.ts`.

### How Stories & Legends works

Every curated site can carry several **separately stored** stories (migration
`0008_heritage_stories`; three tables: `heritage_stories`,
`heritage_story_sources`, `heritage_story_versions`). A story records its
category, its **narrative type** (the epistemic basis: historically documented,
archaeological support, religious text or tradition, mythological narrative,
local folklore, oral history, scholarly interpretation, unverified account,
conflicting accounts, community account), its tradition, language, period,
cultural background, provenance and verification state. These are deliberately
orthogonal: history is not “more true” than a myth, and the API returns the
label rather than a numeric truth score.

The curated seed (`database/seeds/heritage_stories.json`) holds **75 stories for
67 sites**, with **86 story-level sources** (90 source rows including the 4 on
the alternative versions) and **2 alternative versions** for the Konark
origin story, which is itself labelled *conflicting accounts*. Sources are typed
(secondary reference, World Heritage listing, historical record, religious text,
archaeological or government publication, academic research, oral-history
interview, community contribution) and carry author, date, locator, excerpt and
licence where the source provides one. A story is never merged with another
account: the version records are listed side by side with their own traditions,
communities, differences and sources.

`GET /api/heritage/sites/{id}/stories` returns published story cards plus
category facets; `GET /api/heritage/stories/{id}` returns the full narrative
with sources, versions and related stories. `POST /api/heritage/stories/ask` is
**retrieval, not generation**: it tokenises the question, matches stored stories
only (at least two shared terms), joins their summaries and citations, and says
plainly when the library does not cover the question. The map's site payload
carries per-site story counts and story categories, so the explorer can filter
by tradition and show “N stories” before the panel is even opened.

Community oral histories use the same table with `status='pending'`, provenance
`community-oral-history` and consent required. Publication requires an account
listed in `STORY_MODERATOR_EMAILS` (an explicit deployment allowlist, empty by
default, never inferred from a user record). Approval records a **community
account**, not a verified historical claim; rejections are retained but never
public. No automatic source-crawling pipeline is implemented: the curated
corpus is compiled and source-checked by hand, which is why every seeded story
has a real citation rather than a generated one.
On the map (Leaflet + OpenStreetMap, no extra plugin) the initial view fits
India's extent (`6.5–35.8° N, 68.0–97.5° E`, zoom 4). Unselected sites are
grouped by a light lat/lon grid at low zoom (5° at ≤3, 3° at 4, 1.5° at 5) and
drawn as category-coloured circles above it; route stops are always drawn
individually. Choosing a state or region fits the view to the matching records
(`maxZoom` 8), **Reset filters** clears every filter, closes the open site and
returns to the national extent, and a site without a published coordinate is
listed but never plotted. The site list renders ten records at a time and sorts
the filtered set (name, state, category or historical period); cards show the
category letter tile because the curated seed carries no licensed photographs
(no placeholder image is invented).

`POST /api/heritage/route` takes 2+ site ids and returns the ordered stops, per-leg
**great-circle** distances and the total. The UI labels the result *“Approximate
heritage route”*, supports reordering, removal and “Shortest order” (greedy
nearest-neighbour over the published coordinates), and states explicitly that it
is not a road route and has no travel times, opening hours or ticket prices —
because no routing provider is configured and none of those are known here.

Selecting a site shows its metadata, coordinate provenance and every source
reference, then hands off to the rest of the app: **Open subject page** loads that
subject in the existing search/subject overview, and **Search my archives**
pre-fills the Archive Studio keyword search. The map is an entry point into the
same knowledge system, not a separate silo.

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
export (single record, full history, archive and collection reports),
same-origin media proxy, one-command local start, diagnostics endpoint, and the
Heritage Archive Studio: multi-file and ZIP ingestion with safety limits,
PDF/DOCX/PPTX/TXT/Markdown extraction with page/slide references, OCR through
the configured provider, per-file status, duplicate detection, keyword archive
search, per-archive timeline, the India Heritage Explorer map with national
filters and an approximate route builder, the Stories & Legends library
(per-site narratives, epistemic labels, alternative versions, typed sources,
retrieval-only Q&A and reviewed community submissions), and the research
workspace (collections, notes, reports).

**Not verified / not implemented**: a **live Google Cloud Vision** OCR request
(the provider is implemented and covered by mocked-annotate tests, but this
workspace has no GCP credentials — OCR runs through **OCR.space** instead, which
*was* verified live: see [§8](#8-testing-and-production-build)), the
optional Adaption synthesis provider (key authenticates but
the account has no hosted model — inert by design, never faked), PostgreSQL
(supported by config/migrations, only SQLite exercised here), any deployed
environment, email verification / password reset (no email delivery is wired
up, so neither is claimed) and lint/coverage tooling. Also **not implemented**
and therefore not demonstrated: automated story discovery from live external
sources (the first 18 stories are hand-curated and source-checked; the 57 added later are short summaries marked unverified), translated story
versions (the schema and contribution form accept 14 Indian language tags, but
the curated corpus is English), a generative AI storyteller (the “ask” endpoint
retrieves stored stories only), the 3D (GLB/GLTF) viewer, the heritage tour
generator, historical image comparison, a public discovery portal, and
embedding-based semantic search. ZIP handling for large collections is synchronous per file inside the
request's background task, not an external job queue. These are listed in full
in [docs/known-limitations.md](docs/known-limitations.md), [§9](#9-troubleshooting)
and [§12](#12-engineering-notes).

### Feature status

| Capability | Status | Basis |
|---|---|---|
| Historical search, subject overview, related topics, text graph | **Completed** | Live run exercised; live-source unit tests opt-in |
| Image upload + vision analysis | **Completed** | Real provider configured; live run exercised earlier |
| OCR (OCR.space) | **Completed** | Live OCR round trip and end-to-end upload verified |
| OCR (Google Cloud Vision) | **Completed (implementation), live-unverified** | Mocked annotate tests; no GCP credentials here |
| Archive Studio + PDF/DOCX/PPTX extraction | **Completed** | Full backend/frontend suites + live archive run |
| Archive search, timeline, duplicates | **Completed** | Keyword search only; explicitly not semantic |
| India Heritage Explorer + route estimate | **Completed** | 67 sourced sites, all 36 states/UTs; approximate straight-line route |
| Stories & Legends (curated) | **Completed** | 75 stories, 67 sites, 2 alternative traditions |
| Community oral histories + moderation | **Completed (API/UI/tests)** | Pending-by-default; moderator allowlist empty until configured |
| Story entity graph (people/deities/communities) | **Planned** | Not implemented; see limitations |
| Generative storyteller / automatic story discovery | **Planned** | Retrieval-only Q&A is what exists |
| Research workspace, accounts, history, PDF export | **Completed** | Owner-scoped, tested |
| Production deployment / Docker | **Completed** | `docs/deployment.md` is preparation only |

### Database design

The schema is extended by reviewed, additive Alembic revisions: `0001` initial
schema, `0002` record URLs, `0003` analysis interpretation, `0004` analysis
briefing, `0005` accounts/sessions/ownership/history, `0006` the Heritage
Archive Studio, timeline, curated map and research workspace, `0007` the
India-wide heritage metadata (region, period bucket, alternative names,
UNESCO status and multiple source references), and `0008` the Stories & Legends
tables (stories, sources, alternative versions, contributor/consent fields for
community submissions). Ids are
server-generated UUID strings; ownership is a `user_id` foreign key on every row
a user can create, and every read filters on the session user in SQL.

```mermaid
erDiagram
    USERS ||--o{ USER_SESSIONS : "has"
    USERS ||--o{ HISTORY_ENTRIES : "owns"
    USERS ||--o{ ANALYSIS_RUNS : "owns"
    USERS ||--o{ ARCHIVES : "owns"
    USERS ||--o{ RESEARCH_COLLECTIONS : "owns"
    USERS ||--o{ RESEARCH_NOTES : "writes"

    ARCHIVES ||--o{ ARCHIVE_FILES : "contains"
    ARCHIVE_FILES ||--o{ ARCHIVE_PAGES : "text per page"
    ARCHIVE_FILES ||--o{ ARCHIVE_ENTITIES : "mentions"
    ENTITIES ||--o{ ARCHIVE_ENTITIES : "is"
    ARCHIVE_FILES ||--o{ TIMELINE_EVENTS : "dated passages"
    ARCHIVES ||--o{ TIMELINE_EVENTS : "archive timeline"

    ANALYSIS_RUNS ||--o{ ANALYSIS_ENTITIES : "extracted"
    ENTITIES ||--o{ ANALYSIS_ENTITIES : "is"
    ANALYSIS_RUNS ||--o{ RELATIONSHIPS : "proposes"
    ENTITIES ||--o{ RELATIONSHIPS : "source or target"
    RELATIONSHIPS ||--o{ EVIDENCE : "supported by"
    HISTORICAL_RECORDS ||--o{ EVIDENCE : "record"

    RESEARCH_COLLECTIONS ||--o{ COLLECTION_ITEMS : "holds"
    ARCHIVES ||--o{ COLLECTION_ITEMS : "archive"
    ARCHIVE_FILES ||--o{ COLLECTION_ITEMS : "file"
    RESEARCH_COLLECTIONS ||--o{ RESEARCH_NOTES : "notes"

    HERITAGE_SITES ||--o{ HERITAGE_STORIES : "has stories"
    HERITAGE_STORIES ||--o{ HERITAGE_STORY_SOURCES : "cites"
    HERITAGE_STORIES ||--o{ HERITAGE_STORY_VERSIONS : "has traditions"
    HERITAGE_STORY_VERSIONS ||--o{ HERITAGE_STORY_SOURCES : "cites"
    USERS ||--o{ HERITAGE_STORIES : "contributes"
```

`heritage_sites` is intentionally independent of user archives: it is the
curated demonstration dataset: 67 sourced records distributed across all 28
states and 8 Union Territories, labelled `curated-demo-india` and never mixed
with user-contributed material. Uploaded originals are not stored
in the database — `archive_files.storage_key` points at the preserved file under
`HG_STORAGE_DIR`, and extracted text is stored separately per page.

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
| Extracted text | **OCR.space** output when configured; the vision model's reading otherwise — the two are never presented as a verified inscription |
| Historical retrieval | Wikipedia/MediaWiki records with canonical URLs; local seeded store as fallback |
| Briefings | Extractive (quoted) explanations with citation numbers, key facts, dated events, plus explicit limitations |
| Entities & relationships | Normalised entities, candidate relationships with provenance, duplicates collapsed, self-loops excluded |
| Evidence panel | Per-edge records evaluated, counts shown including the zero cases |
| Knowledge graph | Cytoscape graph of the analysis, edge selection → evidence |
| Same-origin media proxy | `GET /api/media` serves allowlisted Wikimedia images from the app's own origin (see §9 for why this matters in Brave and other browsers) |
| Self-diagnosis | `GET /api/diagnostics` reports configuration/DB status and, on request, proves provider connectivity without exposing a secret |
| Demo mode | Explicit `--demo` labelled fixture pipeline for demos without credentials; refused under `ENVIRONMENT=production` |
| Heritage Archive Studio | Owner-scoped archives with multi-file and ZIP upload, per-file processing status, rename/delete, reprocess, stats (`/api/archives*`) |
| Document digitisation | PDF (text layer, page references), DOCX (paragraphs, headings, tables), PPTX (slides, notes), TXT/Markdown; scanned pages and images go through the configured OCR provider |
| Archive search | Page-level keyword search across one archive or all of the account's archives, with matched entities, filters and an explicit `mode: keyword` notice |
| Duplicate detection | SHA-256 identical files, dHash similar photographs, normalised-text fingerprints for documents; reported for review, never silently deleted |
| Archive timeline | Dated passages kept as written (exact, approximate, century, range, BCE/CE), each tied to its file and page |
| Archive & collection PDF reports | Server-rendered report with overview, sources, extracted text, entities, timeline, limitations and provenance |
| India Heritage Explorer | Leaflet + OpenStreetMap, 67 curated sites across all 28 states and 8 Union Territories; category/state/region/period/UNESCO/story filters, searchable paginated list, category-coloured clustered markers, sourced detail panels and an explicitly approximate cross-state route builder |
| Stories & Legends | Per-site story cards grouped by category and filtered in place; separate “alternative traditions” when accounts disagree; epistemic label (`historically documented`, `religious tradition`, `local folklore`, …), language, tradition, source count, full reading view with typed source panel, related stories and back-navigation to the site |
| Story Q&A | `POST /api/heritage/stories/ask` retrieves only the site's stored stories and cites them; it states when the library does not cover the question and never composes an unsourced narrative |
| Community oral histories | Sign-in + consent required; submission stored `pending`, never public until a moderator on `STORY_MODERATOR_EMAILS` approves it, and approval still labels it a community account |
| Research workspace | Collections of archives, notes attached to a subject, and a combined PDF report — all owner-scoped |

---

## 3. Technology stack

Confirmed by the repository (no dependency is listed that is not actually used):

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript 5.9, Vite 8, Tailwind CSS 4, Cytoscape.js, Leaflet 1.9 + OpenStreetMap tiles |
| Frontend tests | Vitest 5 + Testing Library (jsdom) |
| Backend | Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic, httpx, uvicorn |
| Backend tests | pytest (unit + database integration + live-HTTP layers) |
| Database | SQLite for development; PostgreSQL supported by config/driver/migrations |
| Document extraction | Python stdlib (`zipfile` + XML for DOCX/PPTX), `pypdf` for the PDF text layer and `pypdfium2` to rasterise scanned pages for OCR; no external conversion service |
| Image understanding | Any OpenAI-compatible vision endpoint (verified here: Google Gemini's OpenAI-compatible API) |
| OCR | Optional: **OCR.space** (`POST https://api.ocr.space/parse/image`, free key), or **Google Cloud Vision** (`files:annotate`, DOCUMENT_TEXT_DETECTION), or any generic HTTP OCR endpoint |
| Authentication | In-repo: PBKDF2-HMAC-SHA256 (stdlib `hashlib`), opaque sessions stored as SHA-256 digests, HttpOnly cookie |
| PDF | ReportLab (Platypus), server-rendered with an embedded DejaVu Sans for Unicode |
| Historical sources | MediaWiki search/page-images + Wikidata (public, key-free) |
| Optional synthesis | Adaption Labs (grounded-summary only; inert for this key) |
| Graph rendering | Cytoscape.js (client) |
| Tooling | npm scripts at the repository root (`scripts/`), GitHub Actions CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) |

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
an **OCR.space** key (optional, free and issued instantly at
https://ocr.space/ocrapi/freekey), an **Adaption** key (optional, synthesis only). Everything else — accounts, search,
retrieval, photographs, related topics, history, PDF export — runs locally;
historical records come from public key-free APIs. Without a vision key the app
still runs: analysis answers 503 with the exact variables to set, or you can
start the labelled fixture pipeline with `npm run dev -- --demo`.

---

## 5. Quick start

From a fresh clone:

```bash
# 1. Enter the repository
git clone https://github.com/TrivediOm1601/heritagegraph-ai heritagegraph-ai
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
| `OCR_PROVIDER` | no | `ocr_space` (recommended), `google_vision`, or `http` (generic multipart endpoint); unset → `http` |
| `OCR_SPACE_API_KEY` | for `ocr_space` | your OCR.space key (free tier: https://ocr.space/ocrapi/freekey). Backend-only — never a `VITE_*` variable, never in the browser |
| `GOOGLE_APPLICATION_CREDENTIALS` | for Google Vision | path to a service-account JSON key; the adapter mints an OAuth2 token itself. Prefer workload/managed identity in production |
| `GOOGLE_VISION_ACCESS_TOKEN` | alternative | short-lived OAuth2 token, e.g. `gcloud auth print-access-token` in development |
| `GOOGLE_VISION_API_KEY` | alternative | Vision API key, if that is how the project is provisioned |
| `GOOGLE_CLOUD_PROJECT` | no | quota project for user-credential flows (not needed for a service account) |
| `GOOGLE_VISION_ENDPOINT` | no | default `https://vision.googleapis.com/v1/images:annotate` |
| `OCR_API_URL`, `OCR_API_KEY` | for `http` OCR | **set both or neither** — half-configured is reported as an error. Absent → vision-only with an explicit `OCR skipped` warning |
| `OCR_SPACE_API_URL` | no | default `https://api.ocr.space/parse/image` (the official endpoint); override only if you proxy it |
| `OCR_SPACE_LANGUAGE` | no | default `auto` (documented for engines 2/3): no assumption that an inscription is English. A 3-letter code such as `lat`, `ger`, `fre` pins it |
| `OCR_SPACE_ENGINE` | no | default `2` (the documented general-purpose engine); `1` and `3` are the other documented engines |
| `OCR_SPACE_MAX_BYTES` | no | default `1000000` — the free-plan 1 MB file limit; raise it on a paid plan |
| `OCR_TIMEOUT` | no | default 30 s (all OCR providers) |

Verify credentials before use (one real image through the production adapters):

```bash
cd backend && .venv/Scripts/python -m scripts.vision_smoke ../path/to/monument.jpg
# exit 0 = provider answered and output validated · 2 = configuration · 3 = provider call failed

# OCR only — OCR.space needs just OCR_SPACE_API_KEY, so this is the check to run
cd backend && .venv/Scripts/python -m scripts.vision_smoke ../path/to/plaque.jpg --ocr-only

# or both channels in one call
cd backend && .venv/Scripts/python -m scripts.vision_smoke ../path/to/inscription.jpg --ocr

# Google Cloud Vision instead (needs GCP credentials; OCR_PROVIDER=google_vision):
cd backend && OCR_PROVIDER=google_vision \
  GOOGLE_APPLICATION_CREDENTIALS=/abs/path/service-account.json \
  .venv/Scripts/python -m scripts.vision_smoke ../path/to/plaque.jpg --ocr-only
```

The smoke test uses the *selected* provider, so its missing-configuration message
names `OCR_SPACE_API_KEY` for OCR.space, `GOOGLE_*` for Google Vision and
`OCR_API_*` for a generic endpoint — it never tells you to set a variable that
provider does not read. It also reads `backend/.env` exactly like `run_server`
does (the shell wins), so the commands above work without exporting anything
by hand; it prints which files it loaded, never their values.

### Historical sources, media proxy and synthesis

| Variable | Required | Purpose / notes |
|---|---|---|
| `HG_LIVE_SOURCES` | no | `1` queries Wikipedia/Wikidata (public, key-free) for records, photographs and topic metadata; off → local seeded store only |
| `HG_SOURCE_TIMEOUT` | no | default 8 s |
| `HG_SOURCE_RETRIES` | no | attempts per source call for a **transient** failure (429, 5xx, timeout), default `2` (first try + one retry), clamped 1–5. Each call is capped by a 10 s wall-clock budget and permanent 4xx answers are never retried |
| `HG_SOURCE_USER_AGENT` | recommended | User-Agent for the source APIs. Must contain a contact URL or email: Wikimedia **throttles agents without one** (verified: bare name → HTTP 429, contact form → 200). Defaults to `APP_ORIGIN` as the contact |
| `HG_MEDIA_PROXY` | no | `off` loads upstream image URLs directly instead of through `GET /api/media` |
| `HG_MEDIA_USER_AGENT` | recommended | Wikimedia's policy requires a descriptive agent with a contact URL/email — **its CDN answers 403 without one**; default uses `APP_ORIGIN`/localhost |
| `APP_ORIGIN` | no | this deployment's public origin; used as the proxy's default contact |
| `HG_SYNTHESIS` | no | `1` lets the synthesis provider compose one paragraph over the briefing's quoted statements (grounding-checked); off → verbatim quotes only |
| `ADAPTION_API_KEY`, `ADAPTION_MODEL`, `ADAPTION_API_BASE_URL`, `ADAPTION_TIMEOUT` | no | optional synthesis provider; key can live in `backend/.adaption_key` |
| `HG_DEMO_MODE` | no | `1` labelled fixture pipeline; ignored when `ENVIRONMENT=production` |

### Stories & Legends

| Variable | Required | Purpose / notes |
|---|---|---|
| `STORY_MODERATOR_EMAILS` | no | comma/semicolon-separated email allowlist for approving community oral histories. Empty by default: submissions are stored `pending` and nothing is published. Read directly from the process environment at moderation time |

Story content itself is seeded (`python -m scripts.run_server --migrate --seed`) and
stored in the database; no external story API or key is required.

### Authentication and sessions

| Variable | Required | Purpose / notes |
|---|---|---|
| `SESSION_TTL_HOURS` | no | session lifetime in hours, default `168` (7 days), clamped to 1 h … 30 d |
| `TRUST_PROXY_HEADERS` | no | `1` only when a reverse proxy **sets** `X-Forwarded-Proto`; otherwise a client could claim `https` and receive a `Secure` cookie over plain HTTP. Off by default |
| `CORS_ORIGINS` | no | comma-separated allowed origins, for a deployment that serves the frontend from a different origin. `*` is refused (credentialed requests cannot use a wildcard). Same-origin deployments need nothing |

No signing secret is required: the client only ever holds an opaque 256-bit
token, stored server-side as a SHA-256 digest, so there is nothing to verify
with a key. Sessions are revoked on logout and rejected after expiry.

### Heritage Archive Studio

| Variable | Required | Purpose / notes |
|---|---|---|
| `HG_STORAGE_DIR` | no | where uploaded originals are preserved; default `backend/var/archive` (git-ignored). Extract bytes are never stored in the database |
| `HG_ZIP_MAX_ENTRIES` | no | default `60` members inspected per uploaded ZIP |
| `HG_ZIP_MAX_ENTRY_BYTES` | no | default 25 MB per extracted member |
| `HG_ZIP_MAX_TOTAL_BYTES` | no | default 250 MB total uncompressed output |
| `HG_ZIP_MAX_DEPTH` | no | default `1` (the uploaded ZIP only; nested archives are skipped) |

Fixed server-side limits: 25 MB per accepted file, 100 MB per ZIP upload,
20 files per request, 150 MB per multipart request. OCR for archive files uses
the same `OCR_PROVIDER` configuration as image analysis (see above); with no
provider, a scanned document is reported `partial` with its reason rather than
filled with invented text. `pypdf` and `pypdfium2` are installed by
`npm run setup` — they read text-layer PDFs and rasterise scanned pages for OCR.

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
10. **Build an archive** — open **Archive**, create one (name, description,
    region, tags), then add images, PDF/DOCX/PPTX/TXT/Markdown files or a ZIP.
    The file table shows each file's real status, page count and language; open a
    file to read its extracted pages, entities and dated events. **Reprocess**
    retries a failed file, **Download report** produces the archive PDF, and the
    search box under the files searches this archive's extracted text by page.
11. **Explore India's heritage** — open **Map**, which fits the whole country
    and states its curated-dataset provenance. Search or pick any of the 28
    states and 8 Union Territories; combine it with region, category, historical
    period and UNESCO filters, or search within the paginated site list. Choosing
    a marker or list card opens its sourced details and links to its subject page
    and archive search. Add sites from different states and **Build route** for a
    straight-line distance estimate that is labelled approximate, not a road route.
12. **Read and ask about stories** — from a site's details, open **Stories &
    Legends**. Each card states its category, epistemic label, language and
    source count; open one for the full narrative, typed sources and any
    **Alternative traditions**. **Ask about this place** answers only from the
    stored stories and cites them (and says when it cannot). **Contribute a
    local story** requires sign-in and consent and stays `pending` until a
    deployment's moderator approves it — approval labels it a community account,
    not verified history.
13. **Keep research together** — open **Research**, create a collection, add your
    archives to it, write notes against a subject and export the collection
    report. Collections, notes and reports are owner-scoped like everything else.

### Demonstration script (HC26-CULT01)

A reliable 5-minute sequence using only real functionality. Prepare one PDF, one
image with visible text and one photograph, then:

1. `npm run dev`, open the printed App URL, sign in.
2. **Archive** → create *Gujarat Stepwell Heritage Collection* (region Gujarat,
   tags `stepwell, survey`).
3. Add the PDF + image + photograph in one upload. Point at the per-file status:
   the PDF shows its page count, the image its OCR text, the photograph's
   warning when it genuinely contains no text.
4. Open the PDF file → point at the extracted page text with its page numbers,
   the entities and the dated events.
5. Search the archive for a phrase from the document, e.g. `stepwell Patan`.
   Point at the result's page reference; switch to **all archives** to show the
   account-wide search.
6. Scroll to the **Historical timeline** — dates as written, BCE before CE.
7. **Download report** → show the PDF's overview, sources, extraction,
   timeline, limitations.
8. **Map** → show India, choose Gujarat, select Rani ki Vav and Adalaj Stepwell,
   **Build route**, and read the “approximate straight-line” notice aloud.
9. Still on the map, open **Rani ki Vav → Stories & Legends** and read the two
   cards: one *Historically documented*, one *Scholarly interpretation*. Open the
   second, show its typed source panel and “More stories here”, then go back.
10. Open **Sun Temple, Konark → Why was the Sun Temple built? Two traditions**:
    point at *Conflicting accounts* and the two **Alternative traditions**
    (temple tradition vs. dated scholarship), each with its own sources. Ask
    “Which king built the temple?” and show the cited retrieval answer.
11. **Research** → create a collection, add the archive, write a note, export the
    collection report. Then **Home** → run a historical search and open a related
    topic to show the graph/evidence side of the same knowledge system.

Avoid claiming: semantic search, an automated story-discovery crawler, translated
story texts, a generative storyteller, 3D models, real tour routing, or Google
Cloud Vision. Those are not implemented or not verified in this build (see §12).

### Deployment considerations

`npm run build` produces static frontend assets in `frontend/dist`; the backend
runs under any ASGI server. Serve both from one origin and reverse-proxy `/api`
to the backend, preserving `Host` (the CSRF check compares it with `Origin`) and
setting `APP_ORIGIN`, `TRUST_PROXY_HEADERS=1` behind TLS, and an explicit
`CORS_ORIGINS` only if the frontend is served from a different origin. Use
PostgreSQL (`DATABASE_URL`), a persistent volume for `HG_STORAGE_DIR`, and the
platform's secret store for `VISION_API_KEY` / `OCR_SPACE_API_KEY`; never bundle
secrets into the frontend. Migrations run at startup through
`python -m scripts.run_server --migrate` (add `--seed` for the curated records).
Archive processing runs inside the API process's background tasks; put the app
behind a worker-friendly deployment or move ingestion to a job queue before
ingesting hundreds of documents concurrently.

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
chunk-size advisory for the 977 kB bundle; it is not an error.

### Continuous integration

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs the same commands on
every push to `main` and every pull request: the backend suite on Python 3.11,
then `npm ci`, the type check, the frontend suite and the production build on
Node 22. No secrets are configured, because the suite mocks every provider and
the live checks are opt-in. The latest CI result is on the repository's **Actions**
tab. The commands it runs are exactly the ones in the table above, and they pass locally.

### Results from this pass

| Command | Result |
|---|---|
| `npm run setup` | venv + deps verified; migrations applied to head `0008_heritage_stories`; with `backend/.env` moved aside it recreated the file from `backend/.env.example` (non-secret keys only), and the original was restored byte-identical |
| `npm run setup -- --seed` | migrations to head `0008_heritage_stories` + curated seed loaded (idempotent: `historical seed: 0 new record(s) added`; `heritage map seed: 67 curated site(s) upserted`; `story seed: 18 curated story record(s) upserted`) — re-run on 2026-10-31 |
| `cd backend && pytest -q` | **677 passed, 6 skipped** (230.1 s after cleanup), including the archive suites (extraction PDF/DOCX/PPTX/TXT/MD, ZIP traversal/absolute-path/executable/nested-archive rejection, duplicate detection, keyword search, ownership isolation, timeline ordering BCE before CE, archive and collection reports) and the 11-case Stories & Legends suite (seed provenance, separate traditions, typed sources, category/language filters, map story counts, grounded Q&A, off-library refusal, consent-gated pending submissions, allowlisted moderation, idempotent reseeding, seed validation). The skips are the opt-in live-provider tests |
| `cd frontend && npm test` | **118 passed, 2 skipped** (17 files), including the India Heritage Explorer and HeritageMap suites (empty/error states, per-file status, filters, route requirement, notes), StoryLibrary/StoryReader (category filters, alternative versions, sources, back-navigation, consent gate) plus AuthPanel, HistoryPage, SearchGraphPanel and the search failure-path tests |
| `npm run typecheck` | clean (`tsc --noEmit`, tests included) |
| `npm run build` | succeeded (`dist/` 976 kB JS / 48 kB CSS; chunk-size advisory only) |
| `npm run dev` (cleanup pass, explicit unique ports) | both services started, backend `/api/health` `200`, frontend `200`, same-origin proxy `/api/ready` `200`, banner printed the real URLs; the runner process tree was stopped with `taskkill /T`. HTTP probes on the chosen ports still answered after the stop because this machine has several pre-existing dev-server pairs from earlier sessions (and a port-forwarding layer), so **clean shutdown was not conclusively re-verified** — recorded as partial in the submission checklist (D10) |
| Repository cleanup + documentation pass | removed `__pycache__`, `.pytest_cache`, `.dev-logs`, stale logs, `frontend/dist`, `backend/.tmp_archive.db` and ignored browser JSON; no source/test/migration/seed/asset or database deleted; added the audit/cleanup/technical/limitations/schema/deployment docs, security/contribution/checklist files and current screenshots |
| Search failure paths, re-verified on the final build | live search for `taj` and `qutb` → real records through `/api/search` (`200`); backend down → proxy `503` naming the unreachable target (was an empty `502` the browser could only report as a network failure); backend `500` → the backend's own message, not a connection error; backend that never answers → “did not answer within 30 seconds” with the form usable again; one-character query → client-side validation and **no** request in the access log; nonsense query → `200` with the empty state, not an error |
| `npm run dev` start-up ordering | the log now shows `waiting for the backend to answer /api/health before starting the app…` before Vite binds, and the fresh run produced **zero** `ECONNREFUSED` proxy errors (the earlier run served the page ~1 s before uvicorn bound and logged them); `--no-frontend` and `--no-backend` still start their single service, verify it and stop cleanly |
| Live HTTP run against a real uvicorn server + real vision provider (scripted `httpx` client, not a browser) | signup `201` with an `HttpOnly; SameSite=Lax` session cookie · `GET /api/search/graph` returned the subject + 14 record nodes and co-mention edges from real Wikipedia records · a real monument photo reached the configured vision model and the analysis finished `completed` with 2 entities and 8 matched records (this run pre-dates the OCR.space key, so it carried the honest `OCR skipped` warning — since then OCR is configured and the warning is gone) · `GET /api/history` listed both the search and the analysis with their terminal statuses · one-record and full-history PDFs downloaded as valid `%PDF` documents (50 kB / 56 kB) · a second account got `404` for the first account's analysis, history record and export, and an empty history list · logout `204` and the old cookie then answered `401` |
| `npm run check:browser` (Chrome 156) | PASS **7/7** — sign in (account created on first run), search, subject photo + proxy comparison, related-topic navigation, upload t1 (real vision run → `Completed`, 1–3 entities, 5–8 matched records) and t5 (→ `Partial`, 0 entities, warnings shown, **not** a fake success), 0 console errors, 0 responses ≥ 400 |
| `npm run check:browser -- --browser <brave.exe>` (Brave 156) | PASS **6/6** — same flow with one upload (`Completed`, 3 entities, 8 matches), 0 console errors, 0 responses ≥ 400 |
| `npm run check:browser -- --browser <msedge.exe>` (Edge 156) | PASS **7/7** — sign in, search “Sanchi Stupa” with its photograph, related-topic navigation, upload t3 (`Completed`) and t5 (`Completed`, real Latin text read by the vision model), 0 failed requests, 0 unexpected responses ≥ 400 |
| Scripted live API audit (real HTTP, real providers, isolated database) | **133 checks** across auth negatives, 14 search queries, subject images/topics, 4 text graphs, **6 live image analyses**, history/ownership, PDFs, security probes and DB integrity — all pass; timings: search 0.02–6 s (cached vs live source), graph ~6 ms, history ~16 ms, PDF 93–152 ms, analysis 5–11 s |
| **OCR.space live round trip** (`@pytest.mark.live`, real `api.ocr.space`) | `1 passed` — a generated plaque (1900×460 PNG) returned its own text: `HERITAGEGRAPH AI 1843 RECORDS OFFICE`. The controlled local run (artifact not retained in the repository) passed **5/5**: readable plaque text · monument photo with no text → empty result (valid, not an error) · **5.6 MB PNG** recompressed under the 1 MB limit and still read (`CONSECRATED 1843 THE RECORDS OFFICE`) · the 4.8 MB repository photograph accepted · a printed manuscript page (`t5.jpg`, Gothic blackletter at an angle) returned **825 characters** of real OCR output, passed through exactly as read (never cleaned up into something more plausible) · an invalid key → `ProviderAuthenticationError` (HTTP 403) with no key in the message |
| **End-to-end uploaded analysis with OCR.space** (real server; local script not retained) | signup `201` → upload `202` → `completed` in 6.3 s with `extractedText` = `IMPERIAL RECORDS OFFICE ESTABLISHED 1843`, 2 entities, **zero warnings** (the old `OCR skipped` text is gone), history linked to that `analysisId`, `GET /api/diagnostics` → `analysis.ocr: {provider: ocr_space, configured: true, endpoint_host: api.ocr.space, credentials.api_key: true}` — **5/5 checks** |
| **Live archive end-to-end** (real uvicorn, real OCR/vision configuration, scripted `httpx` client) | signup `201` → archive `201` → multipart upload `202` of a generated 2-page PDF, a Markdown field note and a repository monument photograph. All three reached a terminal state; the PDF extracted **2 pages** with page references and produced entities `Patan`, `Rani ki Vav`, `1063 CE`, `1499 CE`, `Adalaj Stepwell`; the Markdown produced `Modhera Sun Temple`, `Bhima I` and a `1026 CE` timeline event; the photograph returned **no OCR text** and was marked completed with the explicit warning *“No readable text was found in this photograph”* — no fabricated inscription. Timeline returned the three dates in order; drive-by search returned page-level excerpts; the archive PDF report was a valid 2-page `%PDF` containing the extraction and its limitations |
| **Live ZIP safety + isolation** (same server) | a ZIP containing `docs/stepwell-notes.md` plus `../escape.txt`, `/absolute.txt`, `tools/run.exe` and a nested `nested.zip` accepted only the safe Markdown and the preserved ZIP, reporting *“4 entries were skipped because they could not be accepted safely.”* — nothing escaped the storage root. Scoped search `200`, global search `200` with an honest `keyword` notice, collection report `200`. A second account saw `total: 0`, got `404` for the archive and its search, and an anonymous timeline request got `401` |
| Manual browser pass, Chromium 156 (`npm run dev`, hash routes `#archive`, `#map`, `#research`) | sign-in through the real form; Archive Studio created an archive and rendered its real stats, upload panel, search and empty timeline; Research workspace rendered its empty state. Console: no JavaScript errors; network: only the expected pre-login `401` session probes |
| Manual India Heritage Explorer pass, Chromium 156 (`npm run dev` → `#map`) | national view at zoom 4 with OSM tiles (`200`), **67 sites / 36 of 36 states and UTs / 14 categories / 0 verified records** and 20 grid clusters; the state picker listed all 36 states and UTs plus “All”; Gujarat → `5 of 67 sites match`, map fit to the state (zoom 8) with one cluster and individual markers; a list click opened the sourced details (coordinates `23.8589, 72.1019 · approximate`, 2 source links) and its marker popup; **Reset filters** returned to zoom 4, `67 of 67 sites match`, 67 records; a cross-state route (Rani ki Vav → Taj Mahal → Hampi) built **2027.4 km** as a dashed straight-line path with the approximate-route notice; **Open subject page** opened the live subject overview for Adalaj Stepwell (5 retrieved sources) and **Search my archives** opened `#archive?q=Charminar` with the query pre-filled; at 390 px the map was 342 px wide with **no horizontal overflow**. No console errors |
| Manual Stories & Legends pass, Chromium 156 (`#map`, live backend) | national summary reported **18 sourced stories across 10 places** and the story filter listed 7 traditions with real counts; **Rani ki Vav** opened 2 cards (History / Architecture) with epistemic labels, language and source counts; “A temple turned upside down” opened the reading view with 2 typed sources, provenance and “More stories here”; **Sun Temple, Konark** opened 3 cards, and *Why was the Sun Temple built? Two traditions* rendered **2 alternative traditions** side by side (Samba's cure vs. Narasimhadeva I), each with its own sources and “differs from” note; the ask box returned an answer naming *Queen Udayamati*, marked *Historically documented*, with the two consulted sources and “draws on 1 of 2 stored stories”; back-navigation returned to the site. No console errors |
| `npm run check:browser` with a text-bearing image (Chromium 156) | **PASS 6/6** — sign in, live search, media-proxy comparison, related-topic navigation, and the upload step showing `Completed` with `extractedText: IMPERIAL RECORDS OFFICE ESTABLISHED 1843` in the UI, 2 entities, 8 matches, no fixture leak, no demo mode. (The check’s “fixture leak” heuristic was tightened: a real Wikipedia record for “Ada Lovelace 1815–1852” matching a query like `1843` is live data, not the demo payload) |
| Brave with the image hosts unreachable (`~NOTFOUND`) | earlier pass, before accounts existed: the subject photograph still rendered through `/api/media` (`renderedDecoded: true`) **while the identical direct upstream load failed** (`net::ERR_NAME_NOT_RESOLVED`); the proxy has not changed since, but this scenario was not re-run against the authenticated build |

### Mocked vs real

| Layer | Real or mocked |
|---|---|
| Backend unit/contract tests | in-process `TestClient`, mocked provider transports |
| Backend database tests | **real** migrated SQLite through the Alembic pipeline and repositories |
| Backend HTTP integration layer | **real** uvicorn process over loopback (`-m integration`) |
| Provider (vision) tests | mocked by default; `HERITAGE_LIVE_PROVIDER_TESTS=1` makes **real** calls |
| OCR tests | **mocked by default** (`httpx.MockTransport` over documented OCR.space bodies and Cloud Vision annotate payloads) plus one opt-in `@pytest.mark.live` OCR.space round trip (`HERITAGE_LIVE_PROVIDER_TESTS=1`) that uploads a generated plaque and asserts its own text. A live Google Cloud Vision call is still not possible without GCP credentials — see [§9](#9-troubleshooting) |
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
| “OCR skipped: OCR service is not configured” | No OCR provider is selected/configured. The message names the variables for the provider you selected: `OCR_SPACE_API_KEY` for `OCR_PROVIDER=ocr_space` (free key: https://ocr.space/ocrapi/freekey), `GOOGLE_*` for `google_vision`, `OCR_API_URL`+`OCR_API_KEY` for `http`. Until then text comes from the vision model only and the run says so instead of inventing an inscription |
| “Google Cloud Vision is not configured …” | The provider is selected but no credential was found. The message names the variables; nothing is sent without a credential |
| OCR 403 / `PERMISSION_DENIED` | For **Google Vision**: the credential is valid but the Vision API is not enabled for that project, or the service account lacks the Vision role — enable `vision.googleapis.com` and grant `roles/cloudvision.user`. For **OCR.space**: a `403` (or a `200` carrying “Invalid or missing API key”) means the key itself is wrong — check `OCR_SPACE_API_KEY`; the adapter never retries an auth failure as if it were transient |
| OCR.space quota / rate limit | Free plan: **500 requests per day per IP**, 25,000/month, 1,000 Engine-3 conversions/month. A `429`, or a `200` body reporting the plan’s limit, is reported as a rate-limit error (with `Retry-After` when the header is present) rather than as “no text” — wait for the window or upgrade the plan |
| OCR.space “file too large” warning | The free plan accepts **1 MB** per file. The adapter recompresses automatically (quality first, resolution only as far as needed to stay legible) and only warns if the image still cannot fit; raise `OCR_SPACE_MAX_BYTES` on a paid plan (5 MB PRO, 100 MB PRO PDF) |
| Extracted text is empty on a document image | With **no OCR configured**, the vision model returned no legible text and the run is `partial` (“No text could be extracted”). With OCR.space configured, an empty result is a real answer: the provider read the image and found nothing legible (verified live on a monument photograph). Check the image contrast/resolution before concluding the document is unreadable |
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
| “Search failed: The HeritageGraph AI backend at http://127.0.0.1:8000 is not reachable” | The request never reached the backend. The dev server now answers such a request with a `503` naming the proxy target it could not reach instead of an empty `502`, so this message means exactly one thing: nothing is listening on that port. Start the whole app with `npm run dev` (it verifies `/api/health` before printing the banner) and read `.dev-logs/backend.log` for why the backend is not up. If you opened the page the moment Vite printed its `Local:` URL, the backend may still have been starting — wait for the `HeritageGraph AI is running` banner, then search again. In production the same message means the reverse proxy is not forwarding `/api` to the backend |
| “Search failed: Could not reach the server (`origin`)” | The browser blocked or lost the request before any HTTP response. With the default same-origin setup this only happens when the page itself was closed/offline; if you set `VITE_API_BASE_URL` to another origin, that origin must allow this one via CORS (see the CORS row) |
| “Search failed: The server did not answer within 30 seconds” | The backend accepted the request but did not reply in time (a live source can be slow). The request is aborted, the form is usable again, and searching again re-runs the retrieval — check `.dev-logs/backend.log` if it repeats |
| `/api/ready` → 503 | Read `checks`: `configuration` names missing variables, `database` means `DATABASE_URL` is unset/unreachable (details are in the server log) |
| Search returns nothing | With `HG_LIVE_SOURCES` off only the seeded records exist; run `npm run setup -- --seed` (or `python -m scripts.run_server --migrate --seed`). With it on, a Wikimedia `429`/outage produces a `notice` in the UI rather than a normal-looking empty archive. Repeated 429s under normal use mean the source is throttling this client: set `HG_SOURCE_USER_AGENT` to a descriptive agent with a contact URL/email (the default uses `APP_ORIGIN`; a bare product name is throttled) |
| The first search looks empty/wrong but repeating it works | Fixed in the transport, not by asking you to retry. A transient source failure (`429`, `5xx`, timeout) is now retried inside the backend (`HG_SOURCE_RETRIES`, default 2 attempts, bounded by a 10 s budget) and once more in the browser for the search and graph requests, so a momentarily busy source no longer surfaces as an empty result. Failed page/media/Wikidata lookups are also no longer cached as “this subject has no text/photograph/type”. If the notice still appears, the source is genuinely down or throttling this client — see the next row |
| The historical explanation is missing but the records are listed | The briefing stage has a 15 s budget (`BRIEFING_BUDGET_SECONDS` in `backend/app/api/routes/search.py`); when the source is too slow the retrieved records are returned with a notice instead of the request hanging until the browser's 30 s timeout |
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
│   │   ├── api/routes/       # health, ready, diagnostics, analysis, search, search-graph, history, auth,
│   │   │                     # graph, evidence, media, archives, research
│   │   ├── core/             # config, error envelope, logging, security (sessions, auth guards, rate limits)
│   │   ├── db/               # session, models, migrations entry point (init_db)
│   │   ├── integrations/     # vision, ocr (+ google_vision), wikipedia, wikimedia_media, wikidata, adaption
│   │   ├── repositories/     # records, entities, analyses, relationships, evidence, users, sessions, history
│   │   ├── schemas/          # the canonical API contract (Pydantic)
│   │   └── services/         # analysis orchestration, retrieval, briefing, extraction, evidence, image,
│   │                         # auth, history, search_graph, export, pdf, archive (ingest, extraction,
│   │                         # search, duplicates, timeline), document, zip, storage, heritage, story,
│   │                         # research, archive_report
│   ├── scripts/              # run_server.py (the supported start path), vision_smoke, probes, eval harness
│   ├── tests/                # unit + database + live-HTTP suites (47 files, incl. archive/extraction/
│   │                         # ownership and the 11-case Stories & Legends suite)
│   └── .env.example          # non-secret local defaults (copied to backend/.env by setup)
├── database/
│   ├── alembic.ini
│   ├── migrations/versions/  # 0001 … 0008 schema chain (0005 = users/sessions/history, 0006 = archives,
│   │                         #   files/pages/timeline/heritage sites/research, 0007 = India heritage metadata,
│   │                         #   0008 = stories, story sources and alternative versions)
│   └── seeds/                # curated development records (historical_records.json), the national
│                             # heritage-site dataset (heritage_sites.json, 67 sites) and the sourced
│                             # story corpus (heritage_stories.json, 75 stories / 86 sources)
├── docs/
│   ├── repository-audit.md   # audit findings, security review, Git/publication risk
│   ├── cleanup-report.md     # what was removed/retained and why
│   ├── technical-approach.md # engineering explanation end to end
│   ├── known-limitations.md  # limitations with impact/workaround/next step
│   ├── database-schema.md    # tables, ER diagram, migrations, ownership
│   ├── deployment.md         # production setup (deployed on Render)
│   ├── screenshots/          # genuine current UI screenshots used by this README
│   └── HeritageGraph-AI.pdf  # earlier report snapshot (28 Oct 2026; not current docs)
├── frontend/
│   ├── src/app/              # React entry point, hash view routing (Home / Archive / Map / Research / History)
│   ├── src/components/       # upload, analysis, briefing, subject, graph, evidence, search-graph,
│   │                         # auth panel, layout, archive (intake, status, files, search, timeline),
│   │                         # heritage (Leaflet map, StoryLibrary, StoryReader)
│   ├── src/hooks/            # analysis submission, auth state, subject route, image fallback
│   ├── src/pages/            # HomePage (search + subject + graph tabs), AnalysisPage, HistoryPage,
│   │                         # ArchivePage, MapPage, ResearchPage
│   ├── src/services/         # api.ts (transport, normalisers), accountApi.ts (auth/history/export),
│   │                         # archiveApi.ts (archives, search, timeline, map, stories, collections)
│   ├── src/types/api.ts      # mirrored contract types (archive/map/story contracts in src/types/archive.ts)
│   └── tests/                # Vitest component/unit suites
├── screenshots/              # earlier curated UI evidence (PNG/WebM); JSON reports are ignored
├── .github/workflows/ci.yml  # backend + frontend tests, typecheck, production build
├── .env.example              # full environment reference (placeholders only)
├── CONTRIBUTING.md           # setup, conventions, tests, PR expectations
├── SECURITY.md               # verified controls, reporting, security limits
├── SUBMISSION_CHECKLIST.md   # honest status of every submission item
├── LICENSE                   # MIT
├── REPAIR_PLAN.md            # repair history and the reasoning behind earlier fixes
└── README.md                 # this guide
```

Local-only state is deliberately outside version control: `.dev-logs/` (runner
logs, recreated by `npm run dev`), `.sidecar/`, `.freebuff/`, `backend/.venv`,
`node_modules`, `frontend/dist`, `backend/var/` (preserved uploads),
`backend/testdata/` (evaluation images) and the secret files
`backend/.vision_key`, `backend/.adaption_key`, `backend/.env`. The local
`backend/heritage.db` contains development accounts and analysis runs from this
machine; it is git-ignored and must never be copied into a release. Only the
curated JSON seeds are distributed.

Where to look for what: **image-analysis logic** →
`backend/app/services/analysis_service.py` (orchestration) and
`frontend/src/pages/AnalysisPage.tsx`; **provider integrations** →
`backend/app/integrations/`; **retrieval & briefings** →
`backend/app/services/retrieval_service.py`, `briefing_service.py`;
**graph/evidence** → `relationship_service.py`, `evidence_service.py` and the
`graph`/`evidence` routes; **archive ingestion and pipeline** →
`archive_service.py` (orchestration), `document_service.py` (PDF/DOCX/PPTX),
`zip_service.py` (safety), `storage_service.py` (originals), `archive_text_service.py`
(search, timeline, duplicates); **map/research** → `heritage_service.py`,
`research_service.py`; **contract** → `backend/app/schemas/` mirrored by
`frontend/src/types/api.ts` and `frontend/src/types/archive.ts`.

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
| `POST /api/archives` · `GET /api/archives` | Create an archive · list this account's archives with stats |
| `GET /api/archives/{id}` · `PATCH /api/archives/{id}` · `DELETE /api/archives/{id}` | Archive detail with per-file status · rename/describe/tag · soft-delete |
| `POST /api/archives/{id}/files` | Multi-file or ZIP upload (`multipart` field `files`) → accepted files + rejected members with reasons |
| `DELETE /api/archive-files/{fileId}` · `POST /api/archive-files/{fileId}/reprocess` | Remove one file · retry its pipeline |
| `GET /api/archives/{id}/search` · `GET /api/archive-search` | Page-level keyword search in one archive · across the account's archives (filters: kind, entity type, language) |
| `GET /api/archives/{id}/timeline` | Dated events with BCE/CE era, precision and source page |
| `GET /api/archives/{id}/report.pdf` · `GET /api/collections/{id}/report.pdf` | Server-rendered archive report · collection report |
| `GET /api/archive-files/{id}` · `GET /api/archive-files/{id}/download` | Extracted pages, entities, timeline and duplicate info · the preserved original |
| `GET /api/heritage/sites` | Curated India-wide sites; filters `q`, `state`, `region`, `category`, `periodGroup`, `unesco`, `story`; `sort`, `limit`, `offset`; response carries filtered total, national summary, per-site story counts/categories, featured stories and complete state/UT facets |
| `POST /api/heritage/route` | Ordered stops and straight-line legs for 2+ selected ids; `order: selection \| distance`; explicitly not a road route |
| `GET /api/heritage/sites/{id}/stories` | Published stories for one site, with category/language filters, epistemic label, provenance counts and category facets |
| `GET /api/heritage/stories/{id}` | One story: full narrative, cultural background, typed sources, alternative versions (each with its own sources) and related stories |
| `POST /api/heritage/stories/ask` | Retrieval-only answer from that site's stored stories, with citations and an explicit limitation when nothing matches |
| `POST /api/heritage/sites/{id}/stories` | Community oral-history submission (`202`, stored `pending`); sign-in, consent and same-origin required |
| `PATCH /api/heritage/stories/{id}/moderation` | Approve/reject a pending contribution; only accounts on `STORY_MODERATOR_EMAILS` (`403` otherwise) |
| `POST /api/collections` · `GET /api/collections` · `GET/PATCH/DELETE /api/collections/{id}` | Research collections |
| `POST/DELETE /api/collections/{id}/items` · `POST /api/collections/{id}/notes` · `PATCH/DELETE /api/notes/{noteId}` | Add/remove archives · add, edit and delete research notes |
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

A later load test added a fourth: **live search degraded to empty results under
repeated use** because the source User-Agent was a bare product name. Wikimedia
throttles agents without contact details; the same request with a contact URL
answers immediately (both measured). `HG_SOURCE_USER_AGENT` now defaults to
`APP_ORIGIN` as its contact and applies to search, page text, Wikidata and
Commons metadata, so a demo no longer runs into `429`s after a dozen searches.

### Scope, limits and roadmap

What is implemented and verified is listed in [§8](#8-testing-and-production-build).
The honest limits of the current build:

* **Not implemented**: automated story discovery/crawling from live cultural
  sources, translated story texts, the 3D GLB/GLTF viewer, the heritage tour
  generator (with a real routing service), historical image comparison, a public
  discovery portal, and embedding-based semantic search. Story Q&A is retrieval
  over stored records, not generation; community stories are accepted but the
  moderation allowlist is empty by default, so nothing is published until a
  deployment sets it. The archive search is keyword search and is labelled
  `mode: keyword` in the API and UI; no embedding provider is configured, so
  calling it semantic would be false.
* **Stories vary in depth.** All 67 sites have a story, but only the first 18 are
  hand-checked; the other 57 are short summaries from the site record and
  Wikipedia, marked `unverified` until a person checks them. The curated corpus
  is English-only (the schema and contribution form accept 14 Indian language
  tags), and the sources are mostly Wikipedia and UNESCO listings — real,
  traceable secondary references, not archival primary documents.
* **DOCX/PPTX extraction** reads paragraphs, headings, tables/text boxes,
  slide text and speaker notes from the package XML. It does not preserve
  complex layout, charts or embedded image contents; those images are not
  re-analysed automatically.
* **OCR quality** depends on the configured provider and language. OCR.space and
  Google Cloud Vision are wired through one adapter; handwritten historical
  material and unsupported scripts can return little or nothing. An empty result
  is reported as empty, never replaced with sample text.
* **Dates** are extracted only when the source states them; approximate and
  century dates keep their precision. Timeline events are extraction candidates,
  not verified historical claims.
* **Coordinates** in the curated dataset are published figures rounded to four
  decimals (`coordinateAccuracy: approximate`), with the source article stored
  on each record. The route builder uses straight-line distance and says so; it
  has no travel times, opening hours or official trail status.
* **Processing** runs in a FastAPI background task per file, not an external job
  queue. It is reliable for a demo-sized archive (the limits above bound it) but
  is not designed for hundreds of concurrent documents; PostgreSQL plus a real
  worker queue is the next step.
* **ZIP uploads** are bounded (60 entries, 250 MB uncompressed, depth 1) and
  unsafe members are skipped with a warning; the ZIP itself is preserved as a
  file for provenance.

### Cross-module work

Cross-module issues and their owners are tracked in
[backend/INTEGRATION_ISSUES.md](backend/INTEGRATION_ISSUES.md); repair history
and the reasoning behind earlier fixes are in [REPAIR_PLAN.md](REPAIR_PLAN.md).
The current limitation register is
[docs/known-limitations.md](docs/known-limitations.md), and the audit/cleanup
evidence is in [docs/repository-audit.md](docs/repository-audit.md) and
[docs/cleanup-report.md](docs/cleanup-report.md).

### License

Released under the [MIT License](LICENSE) — © 2026 OM TRIVEDI. Third-party
assets keep their own terms: the embedded DejaVu fonts carry their notice in
[the fonts directory](backend/app/assets/fonts/LICENSE.txt), and historical text
and photographs are fetched at runtime from Wikipedia/Wikimedia and keep their
own licenses, shown in the UI next to each image.
