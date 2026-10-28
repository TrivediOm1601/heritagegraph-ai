"""B5 tests: the diagnostics route.

Contract under test:

* it reports *presence and shape* of the analysis configuration — never the
  value of a credential, and never the provider URL path;
* it distinguishes "not configured" (free, always answered) from "not
  reachable / rejected" (only checked when the caller asks for a probe);
* the probe is opt-in, and its outcome never includes the provider's body.
"""

from __future__ import annotations

import httpx
import pytest

from app.api.routes import diagnostics as diagnostics_module

SECRET = "vision-key-must-not-leak-9f13"

VISION_ENV = {
    "VISION_API_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "VISION_API_KEY": SECRET,
    "VISION_MODEL": "gemini-3.5-flash-lite",
}


def test_reports_missing_vision_configuration_as_a_problem(client):
    response = client.get("/api/diagnostics")

    assert response.status_code == 200
    body = response.json()
    assert body["analysis"]["mode"] == "live"
    assert body["analysis"]["vision"]["configured"] is False
    assert body["analysis"]["vision"]["model"] is None
    # The exact variables to set are named, so the fix is actionable.
    assert any("VISION_API_BASE_URL" in problem for problem in body["configuration"]["problems"])
    assert body["status"] == "degraded"
    # No probe unless asked for.
    assert body["probe"] is None


def test_reports_configured_vision_without_any_secret_value(client, monkeypatch):
    for name, value in VISION_ENV.items():
        monkeypatch.setenv(name, value)

    response = client.get("/api/diagnostics")

    body = response.json()
    assert response.status_code == 200
    assert body["analysis"]["vision"]["configured"] is True
    assert body["analysis"]["vision"]["model"] == "gemini-3.5-flash-lite"
    # Host only — no path, and certainly no key.
    assert body["analysis"]["vision"]["endpoint_host"] == "generativelanguage.googleapis.com"
    assert body["configuration"]["problems"] == []
    assert SECRET not in response.text
    # The overall status also covers the database, which this unit fixture
    # deliberately leaves unset: vision being ready must not mask that.
    assert body["database"]["configured"] is False
    assert body["status"] == "degraded"


def test_reports_ocr_and_synthesis_as_optional(client, monkeypatch):
    monkeypatch.setenv("ADAPTION_MODEL", "hosted-model-1")
    monkeypatch.setenv("HG_SYNTHESIS", "1")

    body = client.get("/api/diagnostics").json()

    assert body["analysis"]["ocr"]["configured"] is False
    # Requested but unconfigured (no key) must be visible, not silently off.
    assert body["synthesis"] == {
        "requested": True,
        "configured": False,
        "model": "hosted-model-1",
    }


def test_reports_demo_mode_as_such(client, monkeypatch):
    monkeypatch.setenv("HG_DEMO_MODE", "1")

    body = client.get("/api/diagnostics").json()

    assert body["analysis"]["mode"] == "demo"
    # Demo mode needs no credentials, so there is no "problem" to report —
    # the mode itself is the disclosure.
    assert body["configuration"]["problems"] == []


def test_media_proxy_status_is_reported(client, monkeypatch):
    body = client.get("/api/diagnostics").json()
    assert body["media_proxy"]["enabled"] is True
    assert "upload.wikimedia.org" in body["media_proxy"]["hosts"]

    monkeypatch.setenv("HG_MEDIA_PROXY", "off")
    assert client.get("/api/diagnostics").json()["media_proxy"]["enabled"] is False


@pytest.mark.parametrize("status", [200, 401, 500])
def test_probe_reports_the_provider_outcome_without_its_body(client, monkeypatch, status):
    for name, value in VISION_ENV.items():
        monkeypatch.setenv(name, value)

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, content=b'{"error":"provider-secret-detail"}')

    monkeypatch.setattr(
        diagnostics_module,
        "_client_factory",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    response = client.get("/api/diagnostics", params={"probe": "1"})

    body = response.json()
    assert body["probe"]["http_status"] == status
    assert body["probe"]["ok"] is (status == 200)
    assert len(calls) == 1
    assert calls[0].headers["authorization"] == f"Bearer {SECRET}"
    # The probe never echoes the provider's payload.
    assert "provider-secret-detail" not in response.text
    assert SECRET not in response.text


def test_probe_reports_an_unreachable_provider(client, monkeypatch):
    for name, value in VISION_ENV.items():
        monkeypatch.setenv(name, value)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    monkeypatch.setattr(
        diagnostics_module,
        "_client_factory",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(boom), **kwargs
        ),
    )

    body = client.get("/api/diagnostics", params={"probe": "1"}).json()

    assert body["probe"]["ok"] is False
    assert body["probe"]["http_status"] is None
    assert "no route to host" not in body["probe"]["detail"]


def test_probe_is_skipped_when_vision_is_not_configured(client):
    body = client.get("/api/diagnostics", params={"probe": "1"}).json()

    assert body["probe"]["ok"] is False
    assert body["probe"]["http_status"] is None
