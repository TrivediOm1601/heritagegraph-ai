# Submission Checklist — HeritageGraph AI

**Hackathon:** Blithchron '27, IIT Gandhinagar  
**Problem statement:** HC26-CULT01 — Digital Revitalisation of Unstructured
Cultural & Architectural Heritage Archives  
**Prepared:** 31 October 2026  
**Submission mode:** manual (no repository was published and no code was pushed)

Status legend:

- **Verified** — inspected/executed in this pass; evidence recorded.
- **Partial** — partly done or verified earlier, not fully re-checked.
- **Blocked** — waiting on the owner, external credentials or a decision.
- **Not started** — intentionally left for later.
- **N/A** — does not apply to this project.

> Do not mark every row complete. Several rows are owner actions, and the
> deployment rows are preparation only — nothing was deployed.

---

## A. Project directory

| # | Item | Status | Notes |
|---|---|---|---|
| A1 | Repository audit completed | **Verified** | [`docs/repository-audit.md`](docs/repository-audit.md) |
| A2 | Unnecessary temporary files reviewed | **Verified** | Caches, logs, `.dev-logs`, `dist`, temp DB removed |
| A3 | Directory structure reviewed | **Verified** | Existing backend/frontend/database/scripts split retained |
| A4 | Required source code preserved | **Verified** | No source file deleted; `git status` checked |
| A5 | Tests preserved | **Verified** | 47 backend + 18 frontend test files remain |
| A6 | Migrations preserved | **Verified** | `0001`–`0008` present; head applied |
| A7 | Required assets preserved | **Verified** | Fonts, favicon, seeds, screenshots retained |
| A8 | User data protected | **Verified** | Local DB, uploads and testdata retained and ignored; never documented with values |

## B. Security

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Secret scan performed | **Verified** | Exact-value + regex scan; see audit §10 |
| B2 | No known real credentials in files intended for publication | **Verified** | No tracked secret; local key files ignored |
| B3 | Environment files protected | **Verified** | `.env`, key files, `*.db`, uploads ignored |
| B4 | `.env.example` created/updated | **Verified** | Placeholders/comments only |
| B5 | `.gitignore` updated/reviewed | **Verified** | Covers env, keys, DB, uploads, caches, build output |
| B6 | Tracked secret files reviewed | **Verified** | None found (`git ls-files` scan) |
| B7 | User ownership checks reviewed | **Verified** | Owner-scoped SQL + cross-account `404` tests |
| B8 | File upload validation reviewed | **Verified** | Magic-byte image checks, ZIP safety, size limits |
| B9 | Credential rotation advised where exposure is possible | **Partial** | No tracked exposure found; owner must rotate any key shared outside the host |

## C. Documentation

| # | Item | Status | Notes |
|---|---|---|---|
| C1 | README completed | **Verified** | Identity, features, setup, API, tests, limitations, screenshots |
| C2 | Technical approach completed | **Verified** | [`docs/technical-approach.md`](docs/technical-approach.md) |
| C3 | Known limitations completed | **Verified** | [`docs/known-limitations.md`](docs/known-limitations.md) |
| C4 | Deployment instructions completed | **Verified** | [`docs/deployment.md`](docs/deployment.md) — preparation only |
| C5 | Database schema documented | **Verified** | [`docs/database-schema.md`](docs/database-schema.md) |
| C6 | Architecture diagram completed | **Verified** | Mermaid in README and technical approach |
| C7 | Database diagram completed | **Verified** | Mermaid ER in schema doc and README |
| C8 | Installation instructions verified | **Verified** | `npm run setup -- --seed` executed |
| C9 | Testing instructions verified | **Verified** | Commands re-run after cleanup |
| C10 | Troubleshooting documented | **Verified** | README §9 |
| C11 | Contribution guide created | **Verified** | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| C12 | Security guide created | **Verified** | [`SECURITY.md`](SECURITY.md) |
| C13 | Screenshot gallery added | **Verified** | [`docs/screenshots/`](docs/screenshots/) |
| C14 | Earlier PDF report reviewed | **Partial** | Retained as a 28 Oct snapshot; regenerate if submitted as current |

## D. Local development

| # | Item | Status | Notes |
|---|---|---|---|
| D1 | Frontend installation verified | **Partial** | Dependencies present; build/tests pass. A clean `npm ci` was not repeated after cleanup |
| D2 | Backend installation verified | **Verified** | `npm run setup -- --seed` completed |
| D3 | Environment setup verified | **Verified** | Local `.env` loads; templates documented |
| D4 | Database connection verified | **Verified** | `/api/ready` database check ok; revision `0008` |
| D5 | Migrations verified | **Verified** | `--migrate --seed` idempotent |
| D6 | Application startup verified | **Verified** | Running frontend and backend answered HTTP; startup documented |
| D7 | Frontend/backend communication verified | **Verified** | Same-origin proxy search/story requests answered |
| D8 | OCR configuration checked | **Verified** | `/api/diagnostics` reports OCR.space configured (key not shown) |
| D9 | AI integration checked | **Partial** | Configuration checked; live provider result from an earlier pass, not re-run now |
| D10 | Clean shutdown verified | **Partial** | `taskkill /T` stopped the runner's tree, but HTTP probes continued to answer due to pre-existing dev-server pairs/port forwarding on this machine; not conclusively re-verified |

## E. Quality assurance

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Backend tests executed | **Verified** | 677 passed, 6 skipped (after cleanup) |
| E2 | Frontend tests executed | **Verified** | 118 passed, 2 skipped (after cleanup) |
| E3 | Type checking executed | **Verified** | `tsc --noEmit` clean |
| E4 | Production build executed | **Verified** | Vite build succeeded |
| E5 | Database checks executed | **Verified** | Revision, table count, seed counts, `/api/ready` |
| E6 | Security checks executed | **Partial** | Secret scan, ownership/upload tests; no dependency audit or penetration test |
| E7 | Regression checks executed | **Verified** | Full suites after cleanup, same code paths |
| E8 | Coverage/lint measured | **N/A** | No coverage/lint tooling configured; disclosed |

## F. Deployment preparation

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | Production configuration documented | **Verified** | `docs/deployment.md` |
| F2 | Production environment variables documented | **Verified** | Names only; values not included |
| F3 | Backend startup documented | **Verified** | `python -m scripts.run_server` |
| F4 | Frontend build documented | **Verified** | `npm ci && npm run build` |
| F5 | Database setup documented | **Verified** | PostgreSQL DSN + `--migrate --seed` |
| F6 | Health checks documented | **Verified** | `/api/health`, `/api/ready`, `/api/diagnostics` |
| F7 | File storage requirements documented | **Verified** | Persistent `HG_STORAGE_DIR` |
| F8 | Deployment blockers documented | **Verified** | `docs/deployment.md` §11 |
| F9 | Deployment executed | **N/A (excluded)** | Explicitly not performed |
| F10 | Public URL created | **N/A (excluded)** | None |
| F11 | Docker added/tested | **N/A** | Not added; rationale in deployment doc |

## G. Local repository readiness

| # | Item | Status | Notes |
|---|---|---|---|
| G1 | Git status reviewed | **Verified** | Branch `main`, one commit, 80 working-tree entries |
| G2 | Tracked files reviewed | **Verified** | 204 files; no secrets/venv/node_modules/DB |
| G3 | Untracked files reviewed | **Verified** | Essential feature code is untracked — see G6 |
| G4 | Large files reviewed | **Verified** | WebM evidence, PDF, fonts are legitimate |
| G5 | Existing commit history preserved | **Verified** | No rewrite, no new commit, no remote |
| G6 | Current source committed for publication | **Blocked** | Owner must stage/commit the untracked application. Nothing was committed in this pass |
| G7 | Licence reviewed | **Verified** | MIT, © 2026 OM TRIVEDI; third-party assets keep their terms |
| G8 | Repository locally prepared | **Partial** | Cleaned and documented; commit/publication remains owner action |
| G9 | Repository published/pushed | **N/A (excluded)** | Prohibited by the task; no remote configured |

---

## Owner actions before final submission

1. **Commit the untracked application.** The single commit predates the archive
   studio, India map, stories, migrations `0006`–`0008`, their tests and the
   licence. Suggested grouping is in the audit §12. This is the only genuinely
   blocking repository item.
2. **Decide on `docs/HeritageGraph-AI.pdf`.** It is an earlier report; regenerate
   it if it is the submitted report.
3. **Rotate any provider key that has been shared outside this machine.** No key
   is tracked, but the audit cannot know where local values have travelled.
4. **Optionally re-run `npm ci` in `frontend/`** in a clean environment to prove
   installation from the lockfile (D1), and re-check clean shutdown (D10).
5. **If deploying later:** provide PostgreSQL, `APP_ORIGIN`, HTTPS, provider keys,
   storage and backups, then run the deployment checklist. Deployment is not part
   of this submission.
6. **Publish manually when ready.** Do not use an automated push from this
   workspace; no remote is configured and no repository has been created.
