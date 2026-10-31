# Security Policy — HeritageGraph AI

This document describes the verified security controls, the assumptions behind
them and the known limitations of the current build. It is not a claim that the
application is completely secure.

---

## Supported versions

| Version | Supported |
|---|---|
| `0.1.x` (current repository state) | yes — fixes are applied to this line |
| Older snapshots / forks | no |

The project is pre-1.0 and has not been deployed publicly. Security reports are
welcome before and after publication.

---

## Reporting a vulnerability

Please **do not** open a public issue with exploit details.

- Once the repository is published, use the hosting provider's **private
  security advisory** feature (GitHub: *Security → Report a vulnerability*).
- Include the affected route/file, reproduction steps, impact and any suggested
  fix.
- Do not include real provider keys, session cookies, personal data or third-party
  confidential material in the report.
- You should receive an acknowledgement when the report is triaged. No personal
  email address is published in this repository.

If you suspect a credential has been exposed, treat it as compromised and rotate
it with the provider immediately. Do not paste the value into an issue or pull
request.

---

## Secret management

- Provider keys are read **server-side only**; the browser never receives them.
  Vite exposes every `VITE_*` variable to the client, so never name a secret
  with that prefix.
- Local secrets live in `backend/.env`, `backend/.vision_key` and
  `backend/.adaption_key` (or a service-account file referenced by
  `GOOGLE_APPLICATION_CREDENTIALS`). These paths are git-ignored.
- `.env.example` and `backend/.env.example` contain placeholders and comments
  only. They are the only environment files intended for publication.
- In deployment, inject secrets through the platform's secret store or mounted
  files; do not bake them into a container image or the frontend build.
- An exact-value scan during the repository audit found no provider credential in
  any tracked file. If a key has ever been shared outside its host (chat, screen
  recording, log, commit, backup), rotate it; removing a file does not retract a
  disclosure.
- `backend/heritage.db`, `backend/var/`, `backend/testdata/`, `.dev-logs/`,
  `.sidecar/` and `.freebuff/` are local runtime state and must not be published.

---

## Authentication and sessions

- Passwords are hashed with **PBKDF2-HMAC-SHA256** and a per-password salt; no
  plaintext or reversible storage.
- Sessions are opaque 256-bit random tokens. Only a SHA-256 digest is stored
  server-side. The cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` when the
  request is HTTPS (behind a trusted proxy).
- Login/signup/logout are rate-limited. Sessions expire after
  `SESSION_TTL_HOURS` (default 7 days, clamped 1 h–30 d); logout deletes the row.
- There is **no MFA, email verification or password reset** in this build.

## Authorization and data isolation

- Analyses, history entries, archives, files, collections and notes are scoped
  to `user_id` in SQL. The backend never trusts a client-supplied owner id.
- Cross-account reads/writes return `404`, not another user's data.
- PDF exports and file downloads re-check ownership before streaming.
- Community story moderation requires an account on the deployment's
  `STORY_MODERATOR_EMAILS` allowlist (empty by default, so nothing can be
  published).
- Curated heritage/story data is public by design; it contains no user data.

## CSRF and browser boundary

- State-changing routes require a same-origin `Origin` (or `Referer`) that
  matches the request authority or the configured `APP_ORIGIN`/`CORS_ORIGINS`.
  Tests cover the proxied development case and the rejection of an undeclared
  origin.
- The session cookie is `SameSite=Lax`, so it is not sent on ordinary
  cross-site state-changing requests.
- There is no separate CSRF token. If the deployment moves to a split origin or
  a non-browser client, add one.

## File uploads

- Image uploads are validated by **magic bytes**, not filename or client MIME
  type: JPEG, PNG, WebP, GIF, TIFF and AVIF, up to 10 MiB.
- Archive uploads apply extension/content checks, per-file and per-request size
  limits, safe filenames and opaque storage keys. ZIP inspection rejects path
  traversal, absolute paths, executables, nested archives, over-limit entries
  and excessive compression ratios; unsafe members are skipped with a warning.
- Uploaded originals are stored outside the web root and served only through
  owner-scoped routes. They are never embedded in the database.
- The media proxy serves only allowlisted Wikimedia image hosts, with a size
  limit; it does not fetch arbitrary URLs.

## Deployment assumptions

- Serve the frontend and `/api` from one HTTPS origin.
- Set `APP_ORIGIN` to the exact public origin and `TRUST_PROXY_HEADERS=1` only
  when a trusted proxy sets `X-Forwarded-Proto`.
- Keep `/api/diagnostics` restricted; use `/api/health` and `/api/ready` for
  probes.
- Add edge/application rate limiting for public endpoints; only credential
  endpoints are limited in-process.
- Use a least-privilege database role, a persistent upload volume and tested
  backups.
- Do not run with `HG_DEMO_MODE=1` in production (the backend refuses it when
  `ENVIRONMENT=production`).

## Known limitations

The full register is in [`docs/known-limitations.md`](docs/known-limitations.md).
Security-relevant highlights:

- no MFA, email verification or password reset;
- no global API rate limiter or WAF built in;
- no CSRF token (same-origin check + `SameSite=Lax` only);
- no automated dependency-vulnerability scanning in CI;
- no audit log for moderation actions;
- no coverage/lint/security scan in CI;
- SQLite is the local default; PostgreSQL is supported but not exercised in this
  environment;
- Google Cloud Vision has mocked tests only; a live request still needs a GCP
  project and credentials;
- no deployment has been performed, so production hardening is unproven.

## Dependency and secret hygiene

- Dependencies are pinned with compatible ranges in `backend/requirements.txt`
  and `frontend/package.json`; the frontend lockfile is committed.
- CI installs from the lockfile/requirements and runs tests, typecheck and the
  production build without secrets.
- No vulnerability scan (e.g. `npm audit`, `pip-audit`) is run in CI. Run one
  before a public launch and review the results rather than auto-upgrading.
- Report a suspected exposed key privately; do not open a PR that contains the
  value.
