# Deployment Preparation — HeritageGraph AI

> **Status: deployment has not been performed.** This document describes the
> intended production topology and the verified commands/configuration needed to
> run it. it is deployed on Render using the Dockerfile, cloud resource, DNS record or hosting account was
> created during this pass.

---

## 1. Production topology

The application is designed to run as **one origin**:

```text
Browser
  |
  | HTTPS
  v
Reverse proxy / TLS terminator   (serves frontend/dist + /api)
  |                    \
  | /api                \ static files
  v                      v
FastAPI (uvicorn)      frontend/dist
  |
  +--> PostgreSQL
  +--> persistent volume (HG_STORAGE_DIR)
  +--> external providers (vision, OCR, Wikipedia/Wikimedia, map tiles)
```

Serving the SPA and the API from the same origin removes the CORS step and makes
the session cookie first-party. A split-origin deployment is possible only with
`CORS_ORIGINS`, `APP_ORIGIN` and a deliberate cookie/credential review; it is not
the recommended path for this project.

---

## 2. Build and start commands

### Frontend

```bash
cd frontend
npm ci
npm run build        # tsc --noEmit && vite build -> frontend/dist
```

The build was run successfully during verification: 976 kB JS (291 kB gzip) and
48 kB CSS. The chunk-size advisory is not an error.

### Backend

```bash
cd backend
python -m pip install -r requirements.txt
python -m scripts.run_server --host 0.0.0.0 --port 8000
```

`scripts/run_server.py` loads the local `.env` (and optional key files), then
starts uvicorn. Any ASGI server can run `app.main:app` instead, provided the same
environment is supplied. Do **not** use the demo flag in production.

### Database migration (before first start)

```bash
cd backend
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/heritagegraph'
python -m scripts.run_server --migrate --seed
```

`--migrate` is idempotent. `--seed` upserts the curated historical records, 67
heritage sites and 75 stories; it does not overwrite community rows. Never run
destructive migration commands against a database with user data.

---

## 3. Required environment

Names only; values belong in the hosting platform's secret store. The full
reference is in `.env.example`.

| Variable | Required | Notes |
|---|---|---|
| `ENVIRONMENT` | recommended | set `production`; demo mode is refused in production |
| `APP_ORIGIN` | **yes** for public deployment | exact public origin, e.g. `https://heritage.example.org`; used by the same-origin check and secure-cookie decision |
| `DATABASE_URL` | **yes** | PostgreSQL DSN; local default is SQLite |
| `HG_STORAGE_DIR` | strongly recommended | persistent volume for preserved uploads; default `backend/var/archive` |
| `TRUST_PROXY_HEADERS` | if behind TLS proxy | `1` only when the proxy sets `X-Forwarded-Proto` |
| `CORS_ORIGINS` | only for split origins | comma-separated allowlist; never `*` |
| `SESSION_TTL_HOURS` | optional | default 168, clamped 1 h–30 d |
| `VISION_API_BASE_URL`, `VISION_API_KEY`, `VISION_MODEL` | for live analysis | the key is server-side only |
| `OCR_PROVIDER` + provider settings | for OCR | `ocr_space`, `google_vision` or generic HTTP |
| `GOOGLE_APPLICATION_CREDENTIALS` or `GOOGLE_VISION_API_KEY` | for Google Vision | mount the service-account file outside the web root; never commit it |
| `HG_MEDIA_PROXY` | optional | allowlisted Wikimedia image proxy, on by default |
| `HG_SOURCE_USER_AGENT` | recommended | descriptive agent with a contact URL, per Wikimedia policy |
| `STORY_MODERATOR_EMAILS` | optional | allowlist for approving community stories; empty = nobody can publish |
| `HG_ZIP_*`, upload limits | optional | defaults are safe; tune for the deployment |

`APP_ORIGIN` must match the browser's real origin exactly (scheme + host + port).
It is not derived from the request `Host` header.

---

## 4. External services

| Service | Required? | Credential | Failure behaviour |
|---|---|---|---|
| Wikipedia / Wikidata / Wikimedia | yes for live search/images | none (public) | Degraded-source notice; local records may still answer |
| Vision provider (OpenAI-compatible) | yes for image analysis | API key | Analysis fails with a safe message; no fixture fallback |
| OCR.space | for OCR via OCR.space | API key | OCR warning/partial status |
| Google Cloud Vision | optional OCR alternative | ADC/service account or API key | Provider error/partial status; setup documented but not live-verified here |
| OpenStreetMap tiles | for the map | none | Tiles may fail; map controls/markers remain |
| Adaption Labs | optional synthesis | API key | Inert; explanations stay extractive |
| SMTP/email | not implemented | — | No verification/reset email exists |

---

## 5. Security configuration

- Terminate HTTPS at the proxy/load balancer and forward `X-Forwarded-Proto`;
  set `TRUST_PROXY_HEADERS=1` only then. The session cookie becomes `Secure`
  on HTTPS requests.
- Set `APP_ORIGIN` to the public origin; state-changing routes reject a
  mismatched `Origin`.
- Keep provider keys in the platform secret store, injected as environment
  variables or mounted files. Do not bake them into the frontend build; Vite
  exposes every `VITE_*` value to the browser.
- Restrict `/api/diagnostics` at the edge (it reports configuration status and
  can perform an opt-in probe). `/api/health` and `/api/ready` are the safe
  probes.
- Use a least-privilege database user; migrations may need DDL rights.
- Put the upload directory on a persistent volume with restrictive permissions;
  it contains user originals.
- Add edge/application rate limiting for public endpoints. Only credential
  endpoints are rate-limited in-process.
- Back up the database and the upload volume; test a restore.

---

## 6. Health checks

| Endpoint | Meaning | Use |
|---|---|---|
| `GET /api/health` | process is alive; **never** checks dependencies | liveness probe |
| `GET /api/ready` | configuration + real database `SELECT 1`; `503` if a check fails | readiness probe / load-balancer gate |
| `GET /api/diagnostics` | safe configuration/provider status; `?probe=1` makes one provider call | manual operations only; restrict publicly |

Verified locally: `/api/health` `200`; `/api/ready` returned
`{"status":"ready","checks":{"configuration":{...ok},"database":{...ok}}}`.

---

## 7. Reverse proxy outline

The following is an **example** for a single-origin nginx deployment; it was not
executed in this pass. Adapt paths and TLS settings to the host.

```nginx
server {
    listen 443 ssl;
    server_name heritage.example.org;

    root /srv/heritagegraph/frontend/dist;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        client_max_body_size 160m;   # upload contract: 150 MB multipart
        proxy_read_timeout 120s;
    }

    location / {
        try_files $uri $uri/ /index.html;   # hash routing also works without this
    }
}
```

The backend checks the request `Origin` against `APP_ORIGIN`; preserve the
original `Host` and scheme so the check sees the public authority.

---

## 8. Database readiness

- Create an empty PostgreSQL database and a least-privilege role.
- Set `DATABASE_URL=postgresql+psycopg://…`.
- Run `--migrate --seed` once (or as a release job) before starting the app.
- Confirm `/api/ready` reports the database check.
- Configure automated backups, point-in-time recovery where available, and a
  retention policy.
- Only SQLite was exercised locally. PostgreSQL support is present in the
  models/driver/migrations but is **not** live-verified here; run a staging
  migration before production.

---

## 9. File storage

`HG_STORAGE_DIR` holds preserved uploaded originals (opaque keys, not public
URLs). It must be a persistent volume shared by every backend instance. The
database stores page text and metadata, not file bytes. Object storage is not
implemented; replacing the local storage service is a future improvement.

---

## 10. Docker evaluation

A Dockerfile/Compose pair was **not** added. Reasoning:

- the verified local path is `npm run setup` + `npm run dev`, and the production
  path is a static build + one ASGI process; there is no multi-service
  orchestration need for the current scope;
- an untested container would introduce a second deployment path that cannot be
  verified in this environment (no Docker daemon assumed);
- the application needs a writable upload volume and a database, which Compose
  would have to model explicitly rather than hiding.

If a container is required for the submission, build a two-stage image
(Node build → Python runtime), mount `HG_STORAGE_DIR`, inject secrets at runtime
and run the migration as a release step. Test it before claiming support.

---

## 11. Manual blockers before going live

1. Provide production PostgreSQL and run the migrations against it.
2. Configure `APP_ORIGIN`, HTTPS and `TRUST_PROXY_HEADERS`.
3. Provide vision and OCR credentials (and a GCP project if Google Vision is
   desired); verify a live analysis and OCR request.
4. Set `STORY_MODERATOR_EMAILS` if community stories are to be reviewed.
5. Provision persistent storage and backups.
6. Add rate limiting at the edge and restrict `/api/diagnostics`.
7. Run the full test/build suite on the deployment artifact.
8. Perform the deployment checklist; none of it has been executed yet.

---

## 12. Deployment status

- **Deployment performed:** no.
- **Public URL created:** no.
- **Cloud resources created:** no.
- **DNS changed:** no.
- **Domain purchased:** no.
- **Production database migrated:** no.
- **Production secret store configured:** no.

Everything above is preparation and documentation only.
