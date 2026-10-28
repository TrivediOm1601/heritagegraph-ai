"""B5 — shared test fixtures.

Two test layers live here:

* **In-process** (``client``, ``upload``, ``sample_image``): a TestClient
  around a freshly built app — fast contract and validation tests.
* **End-to-end** (``live_server``, ``live_client``): a real uvicorn process
  serving this app over HTTP, started once per session — wire-level
  integration tests, marked ``@pytest.mark.integration``.Environment isolation: the host machine may export configuration
variables of its own (this workspace, for example, exports ``PORT=0``
for its dev-server plumbing), so every variable read by
``app.core.config`` **and the analysis pipeline's** ``HG_DEMO_MODE`` is
cleared here — conftest is imported before any test module, hence
before ``app.main`` loads its module-level settings. The subprocess used
by ``live_server`` receives the same clean environment plus its own
database/demo configuration.

Tests that cannot run until Modules C and D land are kept in the suite
as skipped tests whose ``reason`` starts with an ``INT-nn`` id from
backend/INTEGRATION_ISSUES.md, where each issue is assigned to the
owning module.
"""

import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from app.core.config import CONFIG_ENV_VARS

#: Every environment variable cleared for tests: B1's configuration plus
#: B3's explicit demo-mode opt-in (fixture-based processing) and R2's
#: live-source opt-in — no default test may ever reach the network.
CLEARED_ENV_VARS = (
    *CONFIG_ENV_VARS,
    "HG_DEMO_MODE",
    "HG_LIVE_SOURCES",
    "HG_SOURCE_TIMEOUT",
    # The same-origin image proxy is on by default; a test that wants it off
    # sets HG_MEDIA_PROXY explicitly, and its upstream User-Agent override is
    # never inherited from the host machine.
    "HG_MEDIA_PROXY",
    "HG_MEDIA_USER_AGENT",
    # Provider credentials: no default test may depend on the host
    # machine having (or not having) real keys configured.
    "OCR_API_URL",
    "OCR_API_KEY",
    "OCR_TIMEOUT",
    "VISION_API_BASE_URL",
    "VISION_API_KEY",
    "VISION_MODEL",
    "VISION_TIMEOUT",
)

#: Provider credentials the host actually has. Captured BEFORE the
#: clearing below: the opt-in live tests (``HERITAGE_LIVE_PROVIDER_TESTS=1``)
#: run after this module is imported, so without restoring them here a
#: real provider call could never be made from the suite — the gate
#: alone is not enough.
HOST_PROVIDER_ENV = {
    _name: _value
    for _name in (
        "OCR_API_URL",
        "OCR_API_KEY",
        "VISION_API_BASE_URL",
        "VISION_API_KEY",
        "VISION_MODEL",
    )
    if (_value := (os.environ.get(_name) or "").strip())
}

for _name in CLEARED_ENV_VARS:
    os.environ.pop(_name, None)

if os.environ.get("HERITAGE_LIVE_PROVIDER_TESTS") == "1" and HOST_PROVIDER_ENV:
    os.environ.update(HOST_PROVIDER_ENV)

BACKEND_DIR = Path(__file__).resolve().parents[1]
LIVE_SERVER_LOG = BACKEND_DIR / "live-server.log"


def _make_sample_png() -> bytes:
    """A real, decodable 8x8 PNG — passes D1's content gate, which
    ``POST /api/analysis`` runs on every accepted upload."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (240, 236, 222)).save(buffer, format="PNG")
    return buffer.getvalue()


SAMPLE_PNG = _make_sample_png()


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in CLEARED_ENV_VARS}


@pytest.fixture
def client():
    """In-process TestClient for an app built from explicit default settings."""
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    return TestClient(create_app(Settings.from_env({})))


@pytest.fixture
def sample_image() -> bytes:
    """A small PNG-shaped payload for upload tests."""
    return SAMPLE_PNG


# ---------------------------------------------------------------------------
# Authenticated app fixtures (R7)
# ---------------------------------------------------------------------------

#: Credentials used by the shared authenticated fixtures.
TEST_USER = {"email": "test.user@example.org", "password": "correct horse battery"}
OTHER_USER = {"email": "other.user@example.org", "password": "another valid passphrase"}


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Clear the in-process rate limiters between tests.

    They are intentionally process-wide (a browser cannot reset them), so
    without this every test after the tenth signup would see a 429 that has
    nothing to do with the behaviour under test.
    """
    from app.core import security

    for limiter in (security.limit_credentials, security.limit_analysis, security.limit_export):
        limiter._hits.clear()
    yield


@pytest.fixture
def auth_url(tmp_path, monkeypatch) -> str:
    """Migrated + seeded SQLite database exported as ``DATABASE_URL``.

The history, graph and export tests need real rows; the username/
    password of :data:`TEST_USER` is created by ``auth_client``.
    """
    from alembic import command

    from app.core.config import reset_settings_cache
    from app.db.init_db import alembic_config
    from app.db.session import reset_default_engine

    url = f"sqlite:///{tmp_path / 'app.db'}"
    command.upgrade(alembic_config(url), "head")

    import asyncio

    async def _seed() -> None:
        from app.db.session import create_engine, create_session_factory, session_scope
        from app.repositories.record_repository import load_seed_records, seed_records

        engine = create_engine(url)
        try:
            async with session_scope(create_session_factory(engine)) as session:
                await seed_records(session, load_seed_records())
        finally:
            await engine.dispose()

    asyncio.run(_seed())

    monkeypatch.setenv("DATABASE_URL", url)
    reset_settings_cache()
    reset_default_engine()
    yield url
    reset_settings_cache()
    reset_default_engine()


def _signup(client, credentials: dict[str, str]):
    response = client.post("/api/auth/signup", json=credentials)
    assert response.status_code == 201, response.text
    return response


@pytest.fixture
def auth_client(auth_url):
    """TestClient signed in as :data:`TEST_USER` (cookie already set)."""
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    client = TestClient(create_app(Settings.from_env()))
    _signup(client, TEST_USER)
    return client


@pytest.fixture
def other_client(auth_url):
    """A second signed-in client, for cross-user authorization tests."""
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    client = TestClient(create_app(Settings.from_env()))
    _signup(client, OTHER_USER)
    return client


@pytest.fixture
def upload(auth_client, sample_image):
    """Factory issuing an **authenticated** multipart POST to /api/analysis.

    Auth is required to reach upload validation at all (the route is
    protected), so these tests exercise the real ordering: session →
    upload contract → provider configuration.
    """

    def _upload(
        *,
        content: bytes | None = None,
        filename: str = "document.png",
        content_type: str = "image/png",
        field: str = "file",
        path: str = "/api/analysis",
        headers: dict[str, str] | None = None,
    ):
        return auth_client.post(
            path,
            files={field: (filename, io.BytesIO(sample_image if content is None else content), content_type)},
            headers=headers,
        )

    return _upload


@pytest.fixture(scope="session")
def live_server():
    """A real uvicorn process serving this app (end-to-end layer).

    Provisioned with its own migrated + seeded SQLite database and
    ``HG_DEMO_MODE=1``, so the full upload → process → poll → graph →
    evidence journey runs over real HTTP with fixture providers (no
    credentials exist in this workspace — INT-005). Yields the base URL;
    fails loudly with the server log tail if the process exits or never
    answers ``/api/health``.
    """
    db_dir = tempfile.mkdtemp(prefix="heritagegraph-live-")
    db_url = f"sqlite:///{Path(db_dir) / 'live.db'}"
    _provision_live_database(db_url)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    base_url = f"http://127.0.0.1:{port}"
    log_handle = LIVE_SERVER_LOG.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=str(BACKEND_DIR),
            env={**_clean_env(), "DATABASE_URL": db_url, "HG_DEMO_MODE": "1"},
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
    finally:
        log_handle.close()

    def _log_tail() -> str:
        try:
            return LIVE_SERVER_LOG.read_text(encoding="utf-8", errors="replace")[-2000:]
        except OSError as exc:  # pragma: no cover - diagnostics only
            return f"<unreadable log: {exc}>"

    deadline = time.monotonic() + 30
    ready = False
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"uvicorn exited with code {process.returncode} before becoming "
                    f"ready.\n--- {LIVE_SERVER_LOG.name} ---\n{_log_tail()}"
                )
            try:
                with urllib.request.urlopen(f"{base_url}/api/health", timeout=1) as response:
                    if response.status == 200:
                        ready = True
                        break
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(0.2)
        if not ready:
            raise RuntimeError(
                f"server on {base_url} never answered /api/health.\n"
                f"--- {LIVE_SERVER_LOG.name} ---\n{_log_tail()}"
            )
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()
            process.wait(timeout=10)
        shutil.rmtree(db_dir, ignore_errors=True)


def _provision_live_database(url: str) -> None:
    """Migrate + seed the live server's database (real Alembic + Module C
    seed loader, same code paths the deployment instructions use)."""
    import asyncio

    from alembic import command

    from app.db.init_db import alembic_config

    command.upgrade(alembic_config(url), "head")

    async def _seed() -> int:
        from app.db.session import create_engine, create_session_factory, session_scope
        from app.repositories.record_repository import load_seed_records, seed_records

        engine = create_engine(url)
        sessions = create_session_factory(engine)
        try:
            async with session_scope(sessions) as session:
                return await seed_records(session, load_seed_records())
        finally:
            await engine.dispose()

    asyncio.run(_seed())


@pytest.fixture(scope="session")
def live_client(live_server):
    """httpx client bound to the live server, signed in as a test account.

    The upload/analysis/history endpoints are protected (R7), so the
    end-to-end layer registers one account through the real signup route and
    keeps its session cookie — the same journey a browser takes.
    """
    with httpx.Client(base_url=live_server, timeout=10) as client:
        response = client.post(
            "/api/auth/signup",
            json={"email": "live.tester@example.org", "password": "live test passphrase"},
        )
        assert response.status_code == 201, response.text
        yield client


# ---------------------------------------------------------------------------
# Module C (C2) — async repository test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    """Run anyio-marked tests on asyncio (explicit, no plugin default)."""
    return "asyncio"


@pytest.fixture
def migrated_db(tmp_path) -> Path:
    """A per-test SQLite file migrated by the real Alembic pipeline
    (sync URL — alembic drives its own engine; repositories then open the
    same file through an async engine)."""
    from alembic import command

    from app.db.init_db import alembic_config

    path = tmp_path / "repositories.db"
    command.upgrade(alembic_config(f"sqlite:///{path}"), "head")
    return path


@pytest.fixture
def open_db(migrated_db):
    """Factory: ``async with open_db() as sessions:``.

    The async engine is created AND disposed inside the calling test's own
    event loop, so aiosqlite connections never cross event loops.
    """

    @asynccontextmanager
    async def _factory():
        from sqlalchemy import event

        from app.db.session import create_engine, create_session_factory

        engine = create_engine(f"sqlite+aiosqlite:///{migrated_db}")

        # SQLite does NOT enforce foreign keys unless asked — enable them
        # so every repository test runs against the schema's real rules,
        # exactly as PostgreSQL would enforce them (mirrors the listener in
        # test_schema.py).
        def _enable_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        event.listen(engine.sync_engine, "connect", _enable_foreign_keys)
        try:
            yield create_session_factory(engine)
        finally:
            await engine.dispose()

    return _factory
