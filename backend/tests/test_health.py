"""B1 tests: application import, startup, health and readiness endpoints."""

import importlib

from fastapi.testclient import TestClient

from app.api.routes import health as health_module
from app.core.config import Settings
from app.main import create_app

CONFIGURED = {"DATABASE_URL": "sqlite:///./heritage.db"}


def make_client(**env: str) -> TestClient:
    """Build a client for an app configured from an explicit environment."""
    return TestClient(create_app(Settings.from_env(env)))


def test_application_module_imports():
    module = importlib.import_module("app.main")

    assert callable(module.create_app)
    assert module.app is not None


def test_startup_and_shutdown_lifespan_run():
    with make_client(**CONFIGURED) as client:  # entering runs lifespan
        assert client.get("/api/health").status_code == 200


def test_health_endpoint_reports_ok():
    with make_client(**CONFIGURED) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "HeritageGraph AI"
    assert body["environment"] == "development"


def test_health_stays_ok_while_readiness_fails():
    # No required configuration: readiness must fail, liveness must not.
    with make_client() as client:
        assert client.get("/api/ready").status_code == 503
        assert client.get("/api/health").status_code == 200


def test_ready_reports_missing_required_configuration():
    with make_client() as client:
        response = client.get("/api/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    config_check = body["checks"]["configuration"]
    assert config_check["ok"] is False
    assert "DATABASE_URL" in config_check["detail"]


def test_ready_ok_when_required_configuration_present():
    with make_client(**CONFIGURED) as client:
        response = client.get("/api/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["configuration"]["ok"] is True
    assert all(check["ok"] for check in body["checks"].values())


def test_registered_dependency_failure_is_reported_accurately():
    def failing_check() -> health_module.CheckResult:
        return health_module.CheckResult(ok=False, detail="connection refused")

    health_module.register_dependency_check("database", failing_check)
    try:
        with make_client(**CONFIGURED) as client:
            response = client.get("/api/ready")
    finally:
        health_module.clear_dependency_checks()

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == {
        "ok": False,
        "detail": "connection refused",
    }


def test_crashing_check_fails_readiness_without_leaking_details():
    def exploding_check() -> health_module.CheckResult:
        raise RuntimeError("cannot connect postgres://user:p4ssword@db/x")

    health_module.register_dependency_check("database", exploding_check)
    try:
        with make_client(**CONFIGURED) as client:
            response = client.get("/api/ready")
    finally:
        health_module.clear_dependency_checks()

    assert response.status_code == 503
    detail = response.json()["checks"]["database"]["detail"]
    assert detail == "Check raised RuntimeError"
    assert "p4ssword" not in detail
    assert "cannot connect" not in detail


def test_route_modules_are_registered_under_the_prefix():
    with make_client(**CONFIGURED) as client:
        paths = client.get("/openapi.json").json()["paths"]

    assert "/api/health" in paths
    assert "/api/ready" in paths


def test_unknown_path_returns_404():
    with make_client(**CONFIGURED) as client:
        assert client.get("/api/nope").status_code == 404


# ---------------------------------------------------------------------------
# B5 module-boundary tests.
#
# Module C's repository and Module D's providers do not exist yet (see
# INT-004 / INT-005 in INTEGRATION_ISSUES.md), so what is verified here is
# the contract those modules' checks must satisfy: a failing dependency
# fails readiness — with an assigned, safe message — while liveness stays
# up so orchestration can keep restarting or draining.
# ---------------------------------------------------------------------------


def test_database_unavailable_fails_readiness_but_not_health(client):
    def database_down() -> health_module.CheckResult:
        return health_module.CheckResult(ok=False, detail="connection refused: localhost:5432")

    health_module.register_dependency_check("database", database_down)
    try:
        ready = client.get("/api/ready")
        health = client.get("/api/health")
    finally:
        health_module.clear_dependency_checks()

    assert ready.status_code == 503
    assert ready.json()["status"] == "not_ready"
    assert ready.json()["checks"]["database"] == {
        "ok": False,
        "detail": "connection refused: localhost:5432",
    }
    assert health.status_code == 200


def test_provider_timeout_is_reported_without_leaking_details(client):
    def provider_timeout() -> health_module.CheckResult:
        raise TimeoutError("vision provider timed out with api_key sk-live-secret")

    health_module.register_dependency_check("provider", provider_timeout)
    try:
        ready = client.get("/api/ready")
        health = client.get("/api/health")
    finally:
        health_module.clear_dependency_checks()

    assert ready.status_code == 503
    body = ready.json()
    assert body["checks"]["provider"]["detail"] == "Check raised TimeoutError"
    assert "sk-live-secret" not in ready.text
    assert "vision provider timed out" not in ready.text
    assert health.status_code == 200
