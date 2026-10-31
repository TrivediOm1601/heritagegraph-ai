# Contributing to HeritageGraph AI

Thanks for improving HeritageGraph AI. This is a pre-1.0 project preparing for a
hackathon submission; keep changes focused, tested and honest about what they
verify.

---

## 1. Project layout

| Path | Responsibility |
|---|---|
| `frontend/` | React 19 + Vite + Tailwind UI, Vitest tests |
| `backend/` | FastAPI app, services, integrations, pytest suite |
| `database/` | Alembic migrations and curated JSON seeds |
| `scripts/` | setup, dev runner, test runner, browser check |
| `docs/` | audit, technical approach, limitations, schema, deployment, screenshots |
| `backend/var/` | local preserved uploads (git-ignored, never commit) |

Read [`README.md`](README.md), [`docs/technical-approach.md`](docs/technical-approach.md)
and [`docs/known-limitations.md`](docs/known-limitations.md) before a large
change.

---

## 2. Development setup

Requirements: Node.js 20.19+ (22 recommended), Python 3.11, npm.

```bash
npm run setup          # venv, dependencies, backend/.env if absent, migrations
npm run setup -- --seed   # also load the curated records (idempotent)
npm run dev            # backend + frontend with one command
```

The dev runner prints both URLs and verifies `/api/health` before starting the
frontend. Do not commit `backend/.env`, `backend/.vision_key`,
`backend/.adaption_key`, `backend/heritage.db`, uploads or evaluation data.

To use live providers, copy the relevant comments in `backend/.env.example` into
`backend/.env` and add your own keys. **Never paste a key into an issue, PR,
test fixture, screenshot or documentation.**

---

## 3. Checks before opening a pull request

Run the same commands CI runs:

```bash
npm test                     # backend pytest + frontend vitest
npm run typecheck            # tsc --noEmit
npm run build                # production frontend build
```

For a backend-only or frontend-only loop:

```bash
cd backend  && .venv/Scripts/python -m pytest -q
cd frontend && npm test
```

Live provider/source tests are opt-in (`HERITAGE_LIVE_PROVIDER_TESTS=1`,
`HERITAGE_LIVE_SOURCE_TESTS=1`) and require real credentials. Do not report a
mocked test as proof a live provider works.

---

## 4. Code conventions

- **TypeScript:** strict mode; no `any` without a narrow reason; use the existing
  API normalisers in `src/services/`; keep components accessible (labels, roles,
  keyboard paths).
- **Python:** typed function signatures where the module already uses them;
  async SQLAlchemy repositories; raise the project's canonical errors
  (`app/core/errors.py`) rather than returning ad-hoc error shapes.
- **API contracts:** update `backend/app/schemas/` and the mirrored
  `frontend/src/types/` together. Add a test for a changed response shape.
- **Database:** add a new Alembic revision; never edit an applied migration.
  Migrations must be additive and preserve existing data.
- **UI:** reuse the warm ivory / deep green / terracotta design tokens and the
  shared layout; do not introduce a second design system.
- **Data:** heritage/story records must have a real source. Do not invent names,
  dates, coordinates, legends, inscriptions or images. Preserve uncertainty and
  label traditions accurately.

---

## 5. Branch and commit expectations

- Branch from `main` with a descriptive name (`feat/story-moderation`,
  `fix/ocr-timeout`).
- Keep commits focused. Do not rewrite shared history or force-push.
- Do not commit generated output (`dist/`, caches), local databases, uploads,
  logs, `.env` files or keys.
- A commit message should explain **why** the change exists. Conventional prefixes
  are welcome but not required.
- The owner handles publication/remotes; contributors must not push to a remote
  they do not control.

---

## 6. Pull request checklist

- [ ] The change is scoped and described in the PR body.
- [ ] Backend tests pass; new logic has a test.
- [ ] Frontend tests pass; changed UI has a component test where practical.
- [ ] `npm run typecheck` and `npm run build` pass.
- [ ] No secret, personal data or local database is included.
- [ ] Docs/limitations updated if behaviour, configuration or endpoints changed.
- [ ] Screenshots (if UI) contain no real credentials or private data.

A PR that disables a failing test, weakens an assertion or hides an error will be
asked to fix the underlying cause instead.

---

## 7. Reporting bugs

Include:

1. the command or page and the exact input;
2. expected vs actual behaviour;
3. the sanitised error message/request id (never a key or cookie);
4. environment (OS, Node/Python versions, SQLite/PostgreSQL, provider configured);
5. whether it reproduces with the curated seed and a fresh database.

For security issues, follow [`SECURITY.md`](SECURITY.md), not the public issue
tracker.

---

## 8. Documentation and data contributions

- Documentation must match the code. If you change an endpoint, env var, command
  or limitation, update the README/docs in the same PR.
- For heritage/story contributions, cite a verifiable source and state the
  tradition/community where known. Community accounts stay labelled as such; a
  moderator's approval does not make them verified history.
- Respect copyright: summarise in your own words, keep attribution, and do not
  paste long copyrighted passages or unlicensed images.

---

## 9. Licence

By contributing, you agree that your contribution is released under the
repository's [MIT License](LICENSE). Third-party assets and data keep their own
licences; record attribution where required.
