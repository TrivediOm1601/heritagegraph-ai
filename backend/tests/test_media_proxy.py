"""B5 tests: the same-origin media proxy.

The proxy exists so image *display* never depends on the browser being able
to reach a third-party host. These tests pin both halves of that contract:

* what it refuses (anything that is not an allowlisted https Wikimedia image
  URL, non-image payloads, oversized payloads, redirects off the allowlist,
  and upstream failures) — an image proxy must never become an open proxy;
* what it guarantees on success (the image bytes, a cacheable image
  response, nosniff, and no credentials or user data sent upstream).
"""

from __future__ import annotations

import httpx
import pytest

from app.api.routes import media as media_module

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
THUMB_URL = (
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/3/3c/"
    "Qutb_Minar_2022.jpg/960px-Qutb_Minar_2022.jpg"
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    """The proxy caches images in memory, so each test starts empty."""
    media_module.clear_cache()
    yield
    media_module.clear_cache()


def use_transport(monkeypatch, handler):
    """Point the route's client factory at a mock transport."""
    monkeypatch.setattr(
        media_module,
        "_client_factory",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )


def serve_image(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, content=PNG_BYTES, headers={"content-type": "image/png"}
    )


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------


def test_rejects_a_host_that_is_not_allowlisted(client, monkeypatch):
    calls = []
    use_transport(monkeypatch, lambda request: calls.append(request) or serve_image(request))

    response = client.get("/api/media", params={"url": "https://example.com/photo.jpg"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"
    # The refusal happens before any upstream request is made.
    assert calls == []


@pytest.mark.parametrize(
    "url",
    [
        "http://upload.wikimedia.org/x.jpg",  # plain http
        "https://user:secret@upload.wikimedia.org/x.jpg",  # credentials
        "https://upload.wikimedia.org:8443/x.jpg",  # non-default port
        "https://upload.wikimedia.org.evil.example/x.jpg",  # suffix trick
        "file:///etc/passwd",
    ],
)
def test_rejects_urls_outside_the_allowlist_rules(client, monkeypatch, url):
    use_transport(monkeypatch, serve_image)

    response = client.get("/api/media", params={"url": url})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_rejects_a_non_image_content_type(client, monkeypatch):
    use_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, content=b"<html></html>", headers={"content-type": "text/html"}
        ),
    )

    response = client.get("/api/media", params={"url": THUMB_URL})

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


def test_rejects_an_oversized_image_that_declares_its_size(client, monkeypatch):
    use_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            content=PNG_BYTES,
            headers={"content-type": "image/png", "content-length": str(50 * 1024 * 1024)},
        ),
    )

    response = client.get("/api/media", params={"url": THUMB_URL})

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_stops_reading_an_image_that_exceeds_the_cap_mid_stream(client, monkeypatch):
    # No content-length: the cap must be enforced while streaming, otherwise a
    # hostile upstream could stream unbounded data through the proxy.
    monkeypatch.setattr(media_module, "MAX_IMAGE_BYTES", 64)
    use_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, content=b"x" * 512, headers={"content-type": "image/png"}
        ),
    )

    response = client.get("/api/media", params={"url": THUMB_URL})

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_maps_an_upstream_404_to_the_canonical_not_found(client, monkeypatch):
    use_transport(monkeypatch, lambda request: httpx.Response(404))

    response = client.get("/api/media", params={"url": THUMB_URL})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_reports_an_upstream_server_error_as_unavailable(client, monkeypatch):
    use_transport(monkeypatch, lambda request: httpx.Response(503, content=b"nope"))

    response = client.get("/api/media", params={"url": THUMB_URL})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"


def test_reports_a_transport_failure_as_unavailable(client, monkeypatch):
    def boom(request):
        raise httpx.ConnectError("dns failure", request=request)

    use_transport(monkeypatch, boom)

    response = client.get("/api/media", params={"url": THUMB_URL})

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "dependency_unavailable"
    # The safe message never leaks the upstream host, URL or exception text.
    assert "dns failure" not in body["error"]["message"]
    assert "thumb.wikimedia.org" not in body["error"]["message"]


def test_refuses_a_redirect_off_the_allowlist(client, monkeypatch):
    use_transport(
        monkeypatch,
        lambda request: httpx.Response(
            302, headers={"location": "https://evil.example/tracker.jpg"}
        ),
    )

    response = client.get("/api/media", params={"url": THUMB_URL})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_refuses_a_redirect_loop(client, monkeypatch):
    use_transport(
        monkeypatch,
        lambda request: httpx.Response(
            302, headers={"location": str(request.url)}
        ),
    )

    response = client.get("/api/media", params={"url": THUMB_URL})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"


def test_can_be_disabled_by_environment(client, monkeypatch):
    monkeypatch.setenv("HG_MEDIA_PROXY", "off")
    use_transport(monkeypatch, serve_image)

    response = client.get("/api/media", params={"url": THUMB_URL})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"


# --------------------------------------------------------------------------
# success path
# --------------------------------------------------------------------------


def test_serves_the_image_with_cacheable_safe_headers(client, monkeypatch):
    use_transport(monkeypatch, serve_image)

    response = client.get("/api/media", params={"url": THUMB_URL})

    assert response.status_code == 200
    assert response.content == PNG_BYTES
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]


def test_follows_one_allowed_redirect_and_sends_no_credentials(client, monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url).startswith("https://thumb.wikimedia.org/"):
            return httpx.Response(
                302,
                headers={
                    "location": "https://upload.wikimedia.org/wikipedia/commons/3/3c/Qutb_Minar_2022.jpg"
                },
            )
        return serve_image(request)

    use_transport(monkeypatch, handler)

    response = client.get("/api/media", params={"url": THUMB_URL})

    assert response.status_code == 200
    assert [str(request.url).split("?")[0] for request in seen] == [
        THUMB_URL,
        "https://upload.wikimedia.org/wikipedia/commons/3/3c/Qutb_Minar_2022.jpg",
    ]
    for request in seen:
        # Nothing about the caller travels upstream.
        assert "authorization" not in {key.lower() for key in request.headers}
        assert "cookie" not in {key.lower() for key in request.headers}
        assert request.headers["accept"] == "image/*"


def test_sends_a_user_agent_with_contact_information(client, monkeypatch):
    """Wikimedia answers 403 to agents without a contact URL (verified
    live: the same request passes once one is present), so the proxy must
    always send one — this is what makes images load at all."""
    seen: list[httpx.Request] = []
    use_transport(monkeypatch, lambda request: seen.append(request) or serve_image(request))
    monkeypatch.delenv("HG_MEDIA_USER_AGENT", raising=False)
    monkeypatch.setenv("APP_ORIGIN", "https://heritage.example")

    assert client.get("/api/media", params={"url": THUMB_URL}).status_code == 200

    agent = seen[0].headers["user-agent"]
    assert agent.startswith("HeritageGraphAI/")
    assert "https://heritage.example" in agent


def test_user_agent_override_is_used_verbatim(client, monkeypatch):
    seen: list[httpx.Request] = []
    use_transport(monkeypatch, lambda request: seen.append(request) or serve_image(request))
    monkeypatch.setenv("HG_MEDIA_USER_AGENT", "MyArchive/2.0 (+https://archive.example; ops@archive.example)")

    assert client.get("/api/media", params={"url": THUMB_URL}).status_code == 200

    assert seen[0].headers["user-agent"] == "MyArchive/2.0 (+https://archive.example; ops@archive.example)"


def test_default_user_agent_still_carries_a_contact_url(monkeypatch):
    monkeypatch.delenv("HG_MEDIA_USER_AGENT", raising=False)
    monkeypatch.delenv("APP_ORIGIN", raising=False)

    agent = media_module.user_agent()

    assert "http" in agent and "+" in agent  # a URL, so the host will serve us


def test_repeat_requests_are_served_from_the_cache(client, monkeypatch):
    calls: list[httpx.Request] = []
    use_transport(monkeypatch, lambda request: calls.append(request) or serve_image(request))

    first = client.get("/api/media", params={"url": THUMB_URL})
    second = client.get("/api/media", params={"url": THUMB_URL})

    assert first.content == second.content == PNG_BYTES
    # One upstream request for two viewers: the image host asks for caching.
    assert len(calls) == 1


def test_cache_stays_bounded(client, monkeypatch):
    monkeypatch.setattr(media_module, "CACHE_MAX_ENTRIES", 1)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return serve_image(request)

    use_transport(monkeypatch, handler)
    other = THUMB_URL.replace("Qutb_Minar_2022", "Other_photo")

    client.get("/api/media", params={"url": THUMB_URL})
    client.get("/api/media", params={"url": other})
    client.get("/api/media", params={"url": THUMB_URL})  # evicted above

    assert len(calls) == 3


def test_requires_a_url(client):
    response = client.get("/api/media")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
