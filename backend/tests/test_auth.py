"""R7 — authentication, sessions and authorization tests.

Everything here runs against a real migrated SQLite database and the real
FastAPI app through ``TestClient``. No provider is called.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from conftest import OTHER_USER, TEST_USER


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------


def test_signup_creates_an_account_and_starts_a_session(auth_client):
    body = auth_client.get("/api/auth/me").json()

    assert body["user"]["email"] == TEST_USER["email"].lower()
    assert body["user"]["displayName"] is None
    # No sensitive field is ever returned.
    assert set(body["user"]) == {"id", "email", "displayName"}


def test_signup_normalises_the_email_address(auth_url, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    client = TestClient(create_app(Settings.from_env()))
    response = client.post(
        "/api/auth/signup",
        json={"email": "  MiXeD.Case@Example.ORG ", "password": "a good passphrase"},
    )

    assert response.status_code == 201
    assert response.json()["user"]["email"] == "mixed.case@example.org"


def test_signup_sets_a_hardened_session_cookie(auth_url):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    client = TestClient(create_app(Settings.from_env()))
    response = client.post(
        "/api/auth/signup",
        json={"email": "cookie@example.org", "password": "a good passphrase"},
    )

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    # The token is never in the JSON body.
    assert "hg_session" not in response.text


def test_signup_rejects_short_passwords_and_bad_emails(auth_url):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    client = TestClient(create_app(Settings.from_env()))

    short = client.post(
        "/api/auth/signup", json={"email": "x@example.org", "password": "short"}
    )
    bad_email = client.post(
        "/api/auth/signup", json={"email": "not-an-email", "password": "long enough pass"}
    )

    assert short.status_code == 422
    assert "8 characters" in short.json()["error"]["message"]
    assert bad_email.status_code == 422
    assert "valid email" in bad_email.json()["error"]["message"]


def test_duplicate_signup_is_rejected_and_case_insensitive(auth_client, auth_url):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    client = TestClient(create_app(Settings.from_env()))
    response = client.post(
        "/api/auth/signup",
        json={"email": TEST_USER["email"].upper(), "password": "another passphrase"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_passwords_are_hashed_and_never_stored_in_plaintext(auth_url):
    from app.core.config import Settings
    from app.db.session import create_engine, create_session_factory, session_scope
    from app.repositories.user_repository import get_user_by_email
    from app.services.auth_service import verify_password

    from fastapi.testclient import TestClient
    from app.main import create_app

    client = TestClient(create_app(Settings.from_env()))
    client.post(
        "/api/auth/signup",
        json={"email": "hash@example.org", "password": "super secret passphrase"},
    )

    async def _read() -> str:
        engine = create_engine(auth_url)
        try:
            async with session_scope(create_session_factory(engine)) as session:
                user = await get_user_by_email(session, "hash@example.org")
                assert user is not None
                return user.password_hash
        finally:
            await engine.dispose()

    stored = asyncio.run(_read())
    assert stored.startswith("pbkdf2_sha256$")
    assert "super secret passphrase" not in stored
    assert verify_password("super secret passphrase", stored)
    assert not verify_password("wrong", stored)


# ---------------------------------------------------------------------------
# Login / logout / session lifecycle
# ---------------------------------------------------------------------------


def test_login_returns_the_user_and_rejects_bad_credentials(auth_url):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    client = TestClient(create_app(Settings.from_env()))
    client.post("/api/auth/signup", json=TEST_USER)
    client.post("/api/auth/logout")

    wrong = client.post(
        "/api/auth/login",
        json={"email": TEST_USER["email"], "password": "nope"},
    )
    unknown = client.post(
        "/api/auth/login",
        json={"email": "nobody@example.org", "password": "nope"},
    )
    good = client.post("/api/auth/login", json=TEST_USER)

    # Identical, generic failure — no account enumeration.
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert good.status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_logout_invalidates_the_server_side_session(auth_client):
    cookies = dict(auth_client.cookies)

    assert auth_client.post("/api/auth/logout").status_code == 204
    assert auth_client.get("/api/auth/me").status_code == 401

    # Replaying the old cookie fails because the session row is gone.
    auth_client.cookies.clear()
    for name, value in cookies.items():
        auth_client.cookies.set(name, value)
    assert auth_client.get("/api/auth/me").status_code == 401


def test_expired_sessions_are_rejected_and_removed(auth_client, auth_url):
    from app.db.models import UserSession
    from app.db.session import create_engine, create_session_factory, session_scope
    from app.repositories.user_repository import get_session
    from app.services.auth_service import session_token_hash

    token = auth_client.cookies.get("hg_session")
    assert token

    async def _expire() -> None:
        engine = create_engine(auth_url)
        try:
            async with session_scope(create_session_factory(engine)) as session:
                row = await session.get(
                    UserSession, session_token_hash(token)
                )
                assert row is not None
                row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            async with session_scope(create_session_factory(engine)) as session:
                assert await get_session(session, session_token_hash(token)) is None
        finally:
            await engine.dispose()

    asyncio.run(_expire())
    assert auth_client.get("/api/auth/me").status_code == 401


def test_protected_routes_require_a_session(auth_url):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    anonymous = TestClient(create_app(Settings.from_env()))

    assert anonymous.get("/api/auth/me").status_code == 401
    assert anonymous.get("/api/history").status_code == 401
    assert anonymous.get("/api/history/anything").status_code == 401
    assert anonymous.get("/api/history/export/pdf").status_code == 401
    assert anonymous.get("/api/analysis/some-id/graph").status_code == 401


def test_credential_endpoints_are_rate_limited(auth_url):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    client = TestClient(create_app(Settings.from_env()))
    statuses = [
        client.post(
            "/api/auth/login",
            json={"email": "ratelimit@example.org", "password": "wrong"},
        ).status_code
        for _ in range(12)
    ]

    assert 429 in statuses
    limited = client.post(
        "/api/auth/login",
        json={"email": "ratelimit@example.org", "password": "wrong"},
    )
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


def test_cross_origin_signup_is_blocked(auth_url):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    client = TestClient(create_app(Settings.from_env()))
    response = client.post(
        "/api/auth/signup",
        json={"email": "csrf@example.org", "password": "long enough pass"},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_declared_public_origin_survives_a_host_rewriting_proxy(auth_url, monkeypatch):
    """A proxy that rewrites Host must not break cookie-authenticated writes.

    The Vite dev server and common nginx setups forward to the backend's own
    authority while the browser's ``Origin`` keeps the public one. That is not
    cross-origin: the deployment declares the public address in ``APP_ORIGIN``.
    An *undeclared* origin is still refused (the previous test).
    """
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    monkeypatch.setenv("APP_ORIGIN", "http://127.0.0.1:5173")
    client = TestClient(create_app(Settings.from_env()))
    response = client.post(
        "/api/auth/signup",
        json={"email": "proxied@example.org", "password": "long enough pass"},
        headers={"Origin": "http://127.0.0.1:5173"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["user"]["email"] == "proxied@example.org"


# ---------------------------------------------------------------------------
# Authorization: another user's data is invisible
# ---------------------------------------------------------------------------


def _submit_demo_analysis(client) -> str:
    import io

    from conftest import SAMPLE_PNG

    response = client.post(
        "/api/analysis",
        files={"file": ("plaque.png", io.BytesIO(SAMPLE_PNG), "image/png")},
    )
    assert response.status_code == 202, response.text
    return response.json()["analysisId"]


def test_another_users_analysis_is_a_404(auth_client, other_client, monkeypatch):
    monkeypatch.setenv("HG_DEMO_MODE", "1")
    monkeypatch.setenv("ENVIRONMENT", "test")

    analysis_id = _submit_demo_analysis(auth_client)

    # The owner can read it.
    assert auth_client.get(f"/api/analysis/{analysis_id}").status_code == 200
    # Everybody else sees "not found" — never a 200, never a 403 that
    # would confirm the id exists.
    assert other_client.get(f"/api/analysis/{analysis_id}").status_code == 404
    assert other_client.get(f"/api/analysis/{analysis_id}/graph").status_code == 404
    assert other_client.get(f"/api/history/{analysis_id}").status_code == 404


def test_history_is_private_per_account(auth_client, other_client):
    auth_client.get("/api/search", params={"query": "Ada Lovelace"})

    mine = auth_client.get("/api/history").json()
    theirs = other_client.get("/api/history").json()

    assert mine["total"] >= 1
    assert theirs["total"] == 0
    assert all(item["kind"] == "text_search" for item in mine["items"])
