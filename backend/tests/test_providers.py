"""D2 tests — OCR and vision-model provider adapters.

All default tests are **fixture tests**: they inject an
``httpx.AsyncClient`` backed by ``httpx.MockTransport``, so no network
traffic ever leaves the machine (the adapters tag such results
``provenance="injected-client"``). The single real-call test is marked
``@pytest.mark.live`` and runs only when the operator explicitly opts in
with ``HERITAGE_LIVE_PROVIDER_TESTS=1`` *and* the provider environment
variables are configured — that is the fixture/live distinction required
by D2's acceptance criteria, and the provider test report
(``backend/D2_PROVIDER_TEST_REPORT.md``) records which calls actually
ran.

The autouse fixture below removes every provider variable from the
environment for fixture tests, so results never depend on the host
machine's shell (the live test is exempt: it reads the real
environment).
"""

from __future__ import annotations

import base64
import json
import os

import httpx
import pytest

from app.integrations import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderServerError,
    ProviderTimeoutError,
    classify_http_error,
    read_timeout,
    require_env,
    validate_http_url,
)
from app.integrations.ocr import run_ocr
from app.integrations.vision_model import (
    CHAT_COMPLETIONS_SUFFIX,
    ENV_BASE_URL,
    ENV_KEY,
    ENV_MODEL,
    VisionExtraction,
    chat_completions_url,
    extract_from_image,
)

pytestmark = pytest.mark.anyio

PROVIDER_ENV_VARS = (
    "OCR_API_URL",
    "OCR_API_KEY",
    "OCR_TIMEOUT",
    "VISION_API_BASE_URL",
    "VISION_API_KEY",
    "VISION_MODEL",
    "VISION_TIMEOUT",
    "HERITAGE_LIVE_PROVIDER_TESTS",
)

#: A 1x1 PNG (real bytes — D1's gate would accept it).
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
    "AAAABQABh6FO1AAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def clean_provider_env(monkeypatch, request):
    """Isolate every provider variable except for opted-in live tests."""
    if request.node.get_closest_marker("live"):
        return
    for name in PROVIDER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def set_ocr_env(monkeypatch, *, url="https://ocr.example.test/v1/read", key="sk-test-ocr-key"):
    monkeypatch.setenv("OCR_API_URL", url)
    monkeypatch.setenv("OCR_API_KEY", key)


def set_vision_env(
    monkeypatch,
    *,
    base="https://vision.example.test/v1",
    key="sk-test-vision-key",
    model="vision-model-x",
):
    monkeypatch.setenv("VISION_API_BASE_URL", base)
    monkeypatch.setenv("VISION_API_KEY", key)
    monkeypatch.setenv("VISION_MODEL", model)


def mock_client(handler) -> httpx.AsyncClient:
    """Injected client — results from it are tagged ``injected-client``."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Helper-level tests (env parsing, URL validation)
# ---------------------------------------------------------------------------


def test_require_env_reports_every_missing_name():
    with pytest.raises(ProviderConfigurationError) as info:
        require_env("OCR service", "A_KEY", "B_KEY", environ={})
    message = str(info.value)
    assert "A_KEY" in message and "B_KEY" in message
    assert "not configured" in message


def test_require_env_treats_blank_as_missing():
    with pytest.raises(ProviderConfigurationError):
        require_env("OCR service", "A_KEY", environ={"A_KEY": "   "})


def test_read_timeout_default_invalid_and_bounds():
    assert read_timeout("svc", "T", environ={}) == 30.0
    assert read_timeout("svc", "T", default=60.0, environ={}) == 60.0
    assert read_timeout("svc", "T", environ={"T": "12.5"}) == 12.5
    for bad in ("abc", "0", "-3", "100000"):
        with pytest.raises(ProviderConfigurationError):
            read_timeout("svc", "T", environ={"T": bad})


def test_validate_http_url_rejects_non_http_schemes():
    assert validate_http_url("svc", "U", "https://x.test/api") == "https://x.test/api"
    with pytest.raises(ProviderConfigurationError):
        validate_http_url("svc", "U", "ftp://x.test/api")
    with pytest.raises(ProviderConfigurationError):
        validate_http_url("svc", "U", "not-a-url")


def test_chat_completions_url_normalisation():
    assert chat_completions_url("https://x.test/v1") == "https://x.test/v1/chat/completions"
    assert chat_completions_url("https://x.test/v1/") == "https://x.test/v1/chat/completions"
    suffix = "https://x.test/v1" + CHAT_COMPLETIONS_SUFFIX
    assert chat_completions_url(suffix) == suffix


def test_classify_http_error_mapping_and_retry_after():
    assert classify_http_error(200, provider="p") is None
    assert classify_http_error(302, provider="p") is None
    assert isinstance(classify_http_error(401, provider="p"), ProviderAuthenticationError)
    assert isinstance(classify_http_error(403, provider="p"), ProviderAuthenticationError)
    assert isinstance(classify_http_error(500, provider="p"), ProviderServerError)
    assert isinstance(classify_http_error(418, provider="p"), ProviderError)

    rate = classify_http_error(429, provider="p", retry_after="7")
    assert isinstance(rate, ProviderRateLimitError)
    assert rate.retry_after == 7.0
    # HTTP-date Retry-After values are ignored rather than misparsed.
    date_form = classify_http_error(429, provider="p", retry_after="Wed, 21 Oct 2026 07:28:00 GMT")
    assert isinstance(date_form, ProviderRateLimitError)
    assert date_form.retry_after is None


# ---------------------------------------------------------------------------
# OCR adapter
# ---------------------------------------------------------------------------


async def test_ocr_success_json_request_shape(monkeypatch):
    set_ocr_env(monkeypatch)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"text": "Ada Lovelace 1843"})

    async with mock_client(handler) as client:
        result = await run_ocr(
            PNG_BYTES, filename="notes.png", content_type="image/png", client=client
        )

    request = captured["request"]
    assert request.method == "POST"
    assert str(request.url) == "https://ocr.example.test/v1/read"
    assert request.headers["authorization"] == "Bearer sk-test-ocr-key"
    assert request.headers["content-type"].startswith("multipart/form-data")
    # The image bytes travel inside the multipart body under `image`.
    assert PNG_BYTES in request.content
    assert b'name="image"' in request.content
    assert b"notes.png" in request.content

    assert result.text == "Ada Lovelace 1843"
    assert result.is_empty is False
    assert result.engine == "ocr.example.test"
    assert result.provenance == "injected-client"


async def test_ocr_timeout_config_is_applied_to_the_request(monkeypatch):
    set_ocr_env(monkeypatch)
    monkeypatch.setenv("OCR_TIMEOUT", "12.5")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={"text": ""})

    async with mock_client(handler) as client:
        await run_ocr(PNG_BYTES, client=client)

    assert seen["timeout"] == {
        "connect": 12.5,
        "read": 12.5,
        "write": 12.5,
        "pool": 12.5,
    }


async def test_ocr_success_plain_text_body(monkeypatch):
    set_ocr_env(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="transcribed words", headers={"content-type": "text/plain"})

    async with mock_client(handler) as client:
        result = await run_ocr(PNG_BYTES, client=client)

    assert result.text == "transcribed words"


async def test_ocr_nested_result_key(monkeypatch):
    set_ocr_env(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"text": "nested"}})

    async with mock_client(handler) as client:
        result = await run_ocr(PNG_BYTES, client=client)

    assert result.text == "nested"


async def test_ocr_empty_text_is_a_valid_result(monkeypatch):
    set_ocr_env(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "   "})

    async with mock_client(handler) as client:
        result = await run_ocr(PNG_BYTES, client=client)

    assert result.is_empty is True


async def test_ocr_missing_credentials_fails_before_any_request(monkeypatch):
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"text": ""})

    with pytest.raises(ProviderConfigurationError) as info:
        async with mock_client(handler) as client:
            await run_ocr(PNG_BYTES, client=client)

    message = str(info.value)
    assert "OCR_API_URL" in message and "OCR_API_KEY" in message
    assert called is False


async def test_ocr_invalid_timeout_is_reported_not_silently_defaulted(monkeypatch):
    set_ocr_env(monkeypatch)
    monkeypatch.setenv("OCR_TIMEOUT", "soon")

    with pytest.raises(ProviderConfigurationError) as info:
        await run_ocr(PNG_BYTES, client=mock_client(lambda r: httpx.Response(200, json={})))

    assert "OCR_TIMEOUT" in str(info.value)


async def test_ocr_invalid_url_scheme_is_reported(monkeypatch):
    set_ocr_env(monkeypatch, url="ftp://ocr.example.test/upload")

    with pytest.raises(ProviderConfigurationError) as info:
        await run_ocr(PNG_BYTES, client=mock_client(lambda r: httpx.Response(200, json={})))

    assert "OCR_API_URL" in str(info.value)


async def test_ocr_timeout_is_classified(monkeypatch):
    set_ocr_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    with pytest.raises(ProviderTimeoutError) as info:
        async with mock_client(handler) as client:
            await run_ocr(PNG_BYTES, client=client)

    assert "seconds" in str(info.value)


async def test_ocr_connection_error_is_classified(monkeypatch):
    set_ocr_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ProviderError) as info:
        async with mock_client(handler) as client:
            await run_ocr(PNG_BYTES, client=client)

    assert not isinstance(info.value, ProviderTimeoutError)


@pytest.mark.parametrize("status", [401, 403])
async def test_ocr_auth_failures_are_classified(monkeypatch, status):
    set_ocr_env(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "bad key"})

    with pytest.raises(ProviderAuthenticationError):
        async with mock_client(handler) as client:
            await run_ocr(PNG_BYTES, client=client)


async def test_ocr_rate_limit_carries_retry_after(monkeypatch):
    set_ocr_env(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"})

    with pytest.raises(ProviderRateLimitError) as info:
        async with mock_client(handler) as client:
            await run_ocr(PNG_BYTES, client=client)

    assert info.value.retry_after == 7.0
    assert "Retry after 7 seconds" in str(info.value)


async def test_ocr_server_error_is_classified(monkeypatch):
    set_ocr_env(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream exploded")

    with pytest.raises(ProviderServerError):
        async with mock_client(handler) as client:
            await run_ocr(PNG_BYTES, client=client)


async def test_ocr_malformed_json_is_not_silently_empty(monkeypatch):
    set_ocr_env(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not json", headers={"content-type": "application/json"})

    with pytest.raises(ProviderResponseError) as info:
        async with mock_client(handler) as client:
            await run_ocr(PNG_BYTES, client=client)

    assert "not valid JSON" in str(info.value)


async def test_ocr_json_without_text_field_is_rejected(monkeypatch):
    set_ocr_env(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"answer": 42})

    with pytest.raises(ProviderResponseError) as info:
        async with mock_client(handler) as client:
            await run_ocr(PNG_BYTES, client=client)

    assert "text" in str(info.value)


async def test_ocr_unexpected_content_type_is_rejected(monkeypatch):
    set_ocr_env(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>login</html>", headers={"content-type": "text/html"})

    with pytest.raises(ProviderResponseError):
        async with mock_client(handler) as client:
            await run_ocr(PNG_BYTES, client=client)


async def test_ocr_errors_never_leak_credentials_or_bodies(monkeypatch):
    set_ocr_env(monkeypatch, key="sk-super-secret-ocr-key")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, text="stack trace: /etc/secrets and sk-super-secret-ocr-key"
        )

    with pytest.raises(ProviderError) as info:
        async with mock_client(handler) as client:
            await run_ocr(PNG_BYTES, client=client)

    message = str(info.value)
    assert "sk-super-secret-ocr-key" not in message
    assert "/etc/secrets" not in message
    assert "https://" not in message


async def test_ocr_empty_payload_is_a_programming_error(monkeypatch):
    set_ocr_env(monkeypatch)
    with pytest.raises(ValueError):
        await run_ocr(b"", client=mock_client(lambda r: httpx.Response(200, json={})))


# ---------------------------------------------------------------------------
# Vision-model adapter
# ---------------------------------------------------------------------------

VISION_EXTRACTION = {
    "text": "Portrait of Ada Lovelace",
    "entities": [
        {"name": "Ada Lovelace", "type": "person", "confidence": 0.97},
        {"name": "London", "type": "place"},
    ],
    "relationships": [
        {"source": "Ada Lovelace", "target": "London", "label": "born in", "confidence": 0.9}
    ],
}


def _vision_response(content) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
    )


async def test_vision_success_request_and_validation(monkeypatch):
    set_vision_env(monkeypatch)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return _vision_response(json.dumps(VISION_EXTRACTION))

    async with mock_client(handler) as client:
        result = await extract_from_image(
            PNG_BYTES, filename="doc.png", content_type="image/png", client=client
        )

    request = captured["request"]
    assert str(request.url) == "https://vision.example.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test-vision-key"
    assert request.headers["content-type"] == "application/json"

    body = json.loads(request.content)
    assert body["model"] == "vision-model-x"
    assert body["temperature"] == 0
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "text" and content[0]["text"]
    data_url = content[1]["image_url"]["url"]
    assert data_url.startswith("data:image/png;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1]) == PNG_BYTES

    assert isinstance(result, VisionExtraction)
    assert result.text == "Portrait of Ada Lovelace"
    assert [e.name for e in result.entities] == ["Ada Lovelace", "London"]
    assert result.entities[0].type == "person"
    assert result.entities[1].confidence is None  # absent stays absent, never invented
    assert result.relationships[0].label == "born in"
    assert result.provenance == "injected-client"


async def test_vision_fenced_json_in_content_parts_is_accepted(monkeypatch):
    set_vision_env(monkeypatch)
    fenced = "```json\n" + json.dumps(VISION_EXTRACTION) + "\n```"
    parts = [{"type": "text", "text": fenced}]

    async with mock_client(lambda r: _vision_response(parts)) as client:
        result = await extract_from_image(PNG_BYTES, client=client)

    assert len(result.entities) == 2


async def test_vision_json_wrapped_in_prose_is_recovered(monkeypatch):
    set_vision_env(monkeypatch)
    wrapped = "Here you go:\n" + json.dumps(VISION_EXTRACTION) + "\nHope that helps."

    async with mock_client(lambda r: _vision_response(wrapped)) as client:
        result = await extract_from_image(PNG_BYTES, client=client)

    assert result.relationships[0].source == "Ada Lovelace"


async def test_vision_non_image_content_type_falls_back_to_png(monkeypatch):
    set_vision_env(monkeypatch)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _vision_response(json.dumps(VISION_EXTRACTION))

    async with mock_client(handler) as client:
        await extract_from_image(PNG_BYTES, content_type="application/octet-stream", client=client)

    data_url = captured["body"]["messages"][0]["content"][1]["image_url"]["url"]
    assert data_url.startswith("data:image/png;base64,")


async def test_vision_confidence_is_clamped_to_the_contract_range(monkeypatch):
    set_vision_env(monkeypatch)
    payload = {"entities": [{"name": "Ada", "type": "person", "confidence": 1.7}]}

    async with mock_client(lambda r: _vision_response(json.dumps(payload))) as client:
        result = await extract_from_image(PNG_BYTES, client=client)

    assert result.entities[0].confidence == 1.0


async def test_vision_missing_credentials_lists_every_variable(monkeypatch):
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return _vision_response(json.dumps(VISION_EXTRACTION))

    with pytest.raises(ProviderConfigurationError) as info:
        async with mock_client(handler) as client:
            await extract_from_image(PNG_BYTES, client=client)

    message = str(info.value)
    for name in ("VISION_API_BASE_URL", "VISION_API_KEY", "VISION_MODEL"):
        assert name in message
    assert called is False


async def test_vision_invalid_timeout_is_reported(monkeypatch):
    set_vision_env(monkeypatch)
    monkeypatch.setenv("VISION_TIMEOUT", "-1")

    with pytest.raises(ProviderConfigurationError) as info:
        await extract_from_image(
            PNG_BYTES, client=mock_client(lambda r: _vision_response("{}"))
        )

    assert "VISION_TIMEOUT" in str(info.value)


async def test_vision_timeout_is_classified(monkeypatch):
    set_vision_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=request)

    with pytest.raises(ProviderTimeoutError):
        async with mock_client(handler) as client:
            await extract_from_image(PNG_BYTES, client=client)


async def test_vision_auth_and_rate_limit_and_server_errors(monkeypatch):
    set_vision_env(monkeypatch)

    for status, expected in (
        (401, ProviderAuthenticationError),
        (429, ProviderRateLimitError),
        (500, ProviderServerError),
    ):

        def handler(_request: httpx.Request, status=status) -> httpx.Response:
            return httpx.Response(status, headers={"Retry-After": "3"})

        async with mock_client(handler) as client:
            with pytest.raises(expected):
                await extract_from_image(PNG_BYTES, client=client)


async def test_vision_non_json_content_is_rejected(monkeypatch):
    set_vision_env(monkeypatch)

    async with mock_client(lambda r: _vision_response("I cannot analyse that image.")) as client:
        with pytest.raises(ProviderResponseError) as info:
            await extract_from_image(PNG_BYTES, client=client)

    assert "not valid JSON" in str(info.value)


async def test_vision_complete_json_with_trailing_garbage_is_parsed(monkeypatch):
    """Live regression: the model appends stray tokens after a complete
    JSON document ('...]}}' extra brace, '...]}}nb}'). The document is
    valid — it must be used, not rejected as 'Extra data'."""
    set_vision_env(monkeypatch)
    body = json.dumps(VISION_EXTRACTION)

    for junk in ("}", "nb}", "\nnb}"):
        async with mock_client(
            lambda r, payload=body + junk: _vision_response(payload)
        ) as client:
            result = await extract_from_image(PNG_BYTES, client=client)
        assert result.interpretation is not None or result.entities is not None


async def test_vision_json_array_output_is_rejected(monkeypatch):
    set_vision_env(monkeypatch)

    async with mock_client(lambda r: _vision_response("[1, 2, 3]")) as client:
        with pytest.raises(ProviderResponseError) as info:
            await extract_from_image(PNG_BYTES, client=client)

    assert "not an object" in str(info.value)


async def test_vision_structurally_invalid_output_is_rejected(monkeypatch):
    set_vision_env(monkeypatch)

    async with mock_client(lambda r: _vision_response('{"entities": "not-a-list"}')) as client:
        with pytest.raises(ProviderResponseError) as info:
            await extract_from_image(PNG_BYTES, client=client)

    assert "entities" in str(info.value)


async def test_vision_empty_choices_is_rejected(monkeypatch):
    set_vision_env(monkeypatch)

    async with mock_client(lambda r: httpx.Response(200, json={"choices": []})) as client:
        with pytest.raises(ProviderResponseError) as info:
            await extract_from_image(PNG_BYTES, client=client)

    assert "no completion choices" in str(info.value)


async def test_vision_completion_without_content_is_rejected(monkeypatch):
    set_vision_env(monkeypatch)

    async with mock_client(lambda r: _vision_response("   ")) as client:
        with pytest.raises(ProviderResponseError) as info:
            await extract_from_image(PNG_BYTES, client=client)

    assert "no content" in str(info.value)


async def test_vision_errors_never_leak_credentials(monkeypatch):
    set_vision_env(monkeypatch, key="sk-super-secret-vision-key")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key: sk-super-secret-vision-key")

    with pytest.raises(ProviderError) as info:
        async with mock_client(handler) as client:
            await extract_from_image(PNG_BYTES, client=client)

    message = str(info.value)
    assert "sk-super-secret-vision-key" not in message
    assert "invalid api key" not in message


# ---------------------------------------------------------------------------
# Real provider call (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.live
async def test_live_ocr_round_trip():
    """Executes a REAL OCR call when explicitly enabled and configured.

    Skipped otherwise — a skipped test is reported as skipped, never as a
    pass, in the provider test report.
    """
    if os.environ.get("HERITAGE_LIVE_PROVIDER_TESTS") != "1":
        pytest.skip(
            "live provider tests disabled (set HERITAGE_LIVE_PROVIDER_TESTS=1 to enable)"
        )
    missing = [
        name
        for name in ("OCR_API_URL", "OCR_API_KEY")
        if not (os.environ.get(name) or "").strip()
    ]
    if missing:
        pytest.skip("live OCR not configured (missing " + ", ".join(missing) + ")")

    from PIL import Image
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (64, 32), "white").save(buffer, format="PNG")

    result = await run_ocr(buffer.getvalue(), filename="blank.png", content_type="image/png")

    assert result.provenance == "live"
    assert isinstance(result.text, str)


@pytest.mark.live
async def test_live_vision_round_trip():
    """Executes a REAL vision call when explicitly enabled and configured.

    Skipped otherwise — a skipped test is reported as skipped, never as
    a pass. Run with::

        HERITAGE_LIVE_PROVIDER_TESTS=1 VISION_API_BASE_URL=... \
        VISION_API_KEY=... VISION_MODEL=... pytest -m live

    Asserts only what a live provider legitimately guarantees: a
    validated extraction with ``provenance="live"`` that is not the
    bundled fixture payload (entity names/interpretation come from the
    model, so their *content* is deliberately not asserted).
    """
    if os.environ.get("HERITAGE_LIVE_PROVIDER_TESTS") != "1":
        pytest.skip(
            "live provider tests disabled (set HERITAGE_LIVE_PROVIDER_TESTS=1 to enable)"
        )
    missing = [
        name
        for name in (ENV_BASE_URL, ENV_KEY, ENV_MODEL)
        if not (os.environ.get(name) or "").strip()
    ]
    if missing:
        pytest.skip("live vision not configured (missing " + ", ".join(missing) + ")")

    from app.services.analysis_service import _DEMO_VISION_PAYLOAD

    extraction = await extract_from_image(PNG_BYTES, filename="live.png", content_type="image/png")

    assert extraction.provenance == "live"
    assert isinstance(extraction.entities, list)
    assert isinstance(extraction.relationships, list)
    # A live model must never echo the demo fixture: if it did, the
    # fixture path would be leaking into live mode.
    fixture_names = {
        entity["name"] for entity in _DEMO_VISION_PAYLOAD["entities"]
    }
    assert fixture_names.isdisjoint({entity.name for entity in extraction.entities})
    assert extraction.interpretation != _DEMO_VISION_PAYLOAD["interpretation"]
