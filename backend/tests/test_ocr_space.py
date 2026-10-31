"""OCR.space adapter tests (D2/Phase 8).

Everything except the single ``@pytest.mark.live`` test below is a
**fixture test**: the adapter is called with an injected
``httpx.AsyncClient(httpx.MockTransport(...))`` (tagged
``provenance="injected-client"``), so no network traffic ever leaves the
machine. The live test runs only when ``HERITAGE_LIVE_PROVIDER_TESTS=1``
*and* ``OCR_SPACE_API_KEY`` are set — a skipped test is reported as
skipped, never as a pass.

Covered, per the OCR integration checklist:

1. configuration and provider selection (missing key, bad engine/URL/limit)
2. the exact documented request (POST, ``apikey`` header, ``file`` field,
   ``language``/``OCREngine``/``scale``)
3. parsing: text, empty text, provider errors inside HTTP 200, malformed
   bodies, ``OCRExitCode`` failure codes
4. HTTP-level failures (401, 429, 5xx, timeout, connection)
5. size/format handling (pass-through, recompression, transcode,
   undecodable and un-fittable images)
6. pipeline wiring: extraction picks the provider up, the analysis id
   keeps the extracted text, configuration warnings name the right variables
"""

from __future__ import annotations

import base64
import io
import json
import os
import re

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.integrations import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderServerError,
    ProviderTimeoutError,
)
from app.integrations.ocr import (
    DEFAULT_SPACE_MAX_BYTES,
    DEFAULT_SPACE_URL,
    ENV_SPACE_KEY,
    ENV_SPACE_URL,
    PROVIDER_OCR_SPACE,
    configuration_problem,
    run_ocr,
    selected_provider,
)

pytestmark = pytest.mark.anyio

#: Every variable this file isolates from the host shell.
SPACE_ENV_VARS = (
    "OCR_PROVIDER",
    "OCR_API_URL",
    "OCR_API_KEY",
    "OCR_TIMEOUT",
    ENV_SPACE_KEY,
    ENV_SPACE_URL,
    "OCR_SPACE_LANGUAGE",
    "OCR_SPACE_ENGINE",
    "OCR_SPACE_MAX_BYTES",
    "HERITAGE_LIVE_PROVIDER_TESTS",
)

#: A fake key: the real one belongs in the environment, never in a test.
TEST_KEY = "test-ocr-space-key-1234"

#: A 1x1 PNG (real bytes — D1's magic-byte gate accepts it).
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
    "AAAABQABh6FO1AAAAABJRU5ErkJggg=="
)

#: Sample text a readable plaque would carry — asserted against the live
#: provider below.
PLAQUE_TEXT = "HERITAGEGRAPH AI 1843 RECORDS OFFICE"


@pytest.fixture(autouse=True)
def clean_space_env(monkeypatch, request):
    """Isolate every OCR variable except for opted-in live tests."""
    if request.node.get_closest_marker("live"):
        return
    for name in SPACE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------- helpers


def env(**overrides: str) -> dict[str, str]:
    """Environment for an OCR.space call (provider + key by default)."""
    base = {"OCR_PROVIDER": PROVIDER_OCR_SPACE, ENV_SPACE_KEY: TEST_KEY}
    base.update(overrides)
    return base


def space_body(
    text: str | None = "READABLE TEXT",
    *,
    exit_code: int = 1,
    errored: bool = False,
    message: str | None = None,
    include_results: bool = True,
) -> dict:
    """A response shaped exactly like the documented one."""
    results = None
    if include_results:
        results = [
            {
                "FileParseExitCode": 1,
                "ParsedText": text,
                "ErrorMessage": None,
                "ErrorDetails": None,
                "TextOverlay": None,
            }
        ]
    return {
        "ParsedResults": results,
        "OCRExitCode": exit_code,
        "IsErroredOnProcessing": errored,
        "ErrorMessage": message,
        "ErrorDetails": None,
        "ProcessingTimeInMilliseconds": "412",
        "SearchablePDFURL": None,
        "TextOrientation": None,
    }


def json_response(body: dict, status: int = 200, headers: dict | None = None):
    return httpx.Response(status, json=body, headers=headers or {})


def capturing_client(respond):
    """Client whose handler records the outgoing request, then answers."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        seen["body"] = request.content
        return respond(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client, seen


def multipart_parts(request: httpx.Request) -> dict[str, bytes]:
    """Split the outgoing multipart body into ``field name -> payload``.

    The name is read with a leftmost match so ``filename=`` (which also
    contains ``name=``) can never be mistaken for the field name itself.
    """
    boundary = request.headers["content-type"].split("boundary=")[-1].strip().encode()
    parts: dict[str, bytes] = {}
    for chunk in request.content.split(b"--" + boundary):
        header, sep, payload = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        match = re.search(rb'name="([^"]+)"', header)
        if not match:
            continue
        parts[match.group(1).decode()] = payload.rstrip(b"\r\n")
    return parts


def idle_client() -> httpx.AsyncClient:
    """A client for calls that must fail before any network traffic."""
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: json_response(space_body())))


def png(width: int = 32, height: int = 24, color=(255, 255, 255)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def noisy_png(width: int = 1600, height: int = 1200) -> bytes:
    """Incompressible pixels: a PNG that is genuinely over the file limit
    but still fits once it is JPEG-compressed."""
    image = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    return _as_png(image)


def _as_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def webp_bytes() -> bytes:
    """A format OCR.space does not document — the adapter must transcode."""
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (240, 240, 240)).save(buffer, format="WEBP")
    return buffer.getvalue()


def _vision_response(payload: dict) -> httpx.Response:
    """The chat-completions envelope the vision adapter reads."""
    return httpx.Response(
        200,
        json={
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": json.dumps(payload)}}
            ]
        },
    )


def _vision_client(payload: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: _vision_response(payload))
    )


def plaque_image(text: str = PLAQUE_TEXT) -> bytes:
    """A generated plaque: black serif text on a light stone background.

    The canvas is wide enough for the whole phrase, so a truncated result
    would mean the OCR lost characters rather than the image losing them.
    """
    image = Image.new("RGB", (1900, 460), (238, 232, 216))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(
        os.path.join("app", "assets", "fonts", "DejaVuSans.ttf"), 64
    )
    draw.text((70, 190), text, fill=(28, 24, 20), font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- selection


def test_provider_selection_accepts_ocr_space():
    assert selected_provider({"OCR_PROVIDER": "ocr_space"}) == PROVIDER_OCR_SPACE
    assert selected_provider({"OCR_PROVIDER": "OCR_SPACE"}) == PROVIDER_OCR_SPACE


def test_unsupported_provider_is_named():
    with pytest.raises(ProviderConfigurationError) as info:
        selected_provider({"OCR_PROVIDER": "tesseract-cloud"})
    assert "tesseract-cloud" in str(info.value)


async def test_missing_key_names_ocr_space_api_key_before_any_request():
    client, seen = capturing_client(lambda request: json_response(space_body()))

    with pytest.raises(ProviderConfigurationError) as info:
        await run_ocr(
            PNG_1PX,
            filename="x.png",
            content_type="image/png",
            client=client,
            environ={"OCR_PROVIDER": PROVIDER_OCR_SPACE},
        )

    message = str(info.value)
    assert ENV_SPACE_KEY in message
    # It never asks for a variable belonging to a different provider.
    assert "OCR_API_KEY" not in message and "GOOGLE_" not in message
    assert "request" not in seen  # no traffic for a config error


def test_configuration_problem_is_none_with_a_key_and_names_the_key_without():
    assert configuration_problem(env()) is None
    problem = configuration_problem({"OCR_PROVIDER": PROVIDER_OCR_SPACE})
    assert problem and ENV_SPACE_KEY in problem


async def test_blank_key_counts_as_missing():
    client, _ = capturing_client(lambda request: json_response(space_body()))
    with pytest.raises(ProviderConfigurationError):
        await run_ocr(PNG_1PX, client=client, environ=env(**{ENV_SPACE_KEY: "  "}))


# --------------------------------------------------------------------------- request


async def test_request_matches_the_documented_contract():
    client, seen = capturing_client(lambda request: json_response(space_body("MATCH")))

    result = await run_ocr(
        PNG_1PX,
        filename="plaque.png",
        content_type="image/png",
        client=client,
        environ=env(),
    )

    request = seen["request"]
    assert str(request.url) == DEFAULT_SPACE_URL
    assert request.method == "POST"
    # The key travels in the documented request header, never the URL.
    assert request.headers["apikey"] == TEST_KEY
    assert TEST_KEY not in str(request.url)
    assert "content-type" in request.headers and "multipart/form-data" in request.headers["content-type"]

    parts = multipart_parts(request)
    assert "file" in parts, "the documented upload field is `file`"
    assert parts["file"].startswith(b"\x89PNG")  # bytes passed through untouched
    assert b"plaque.png" in request.content
    # language: `auto`, not English — a historical inscription is often not.
    assert b'name="language"' in request.content and b"auto" in parts.get("language", b"")
    assert b'name="OCREngine"' in request.content
    assert parts["OCREngine"] == b"2"  # documented general-purpose default

    assert result.provider == PROVIDER_OCR_SPACE
    assert result.provenance == "injected-client"
    assert result.text == "MATCH"
    assert not result.is_empty


async def test_language_and_engine_are_configurable():
    client, seen = capturing_client(lambda request: json_response(space_body("LATIN")))

    await run_ocr(
        PNG_1PX,
        client=client,
        environ=env(**{"OCR_SPACE_LANGUAGE": "lat", "OCR_SPACE_ENGINE": "3"}),
    )

    parts = multipart_parts(seen["request"])
    assert parts["language"] == b"lat"
    assert parts["OCREngine"] == b"3"


@pytest.mark.parametrize("engine", ["0", "4", "two"])
async def test_invalid_engine_is_a_configuration_error(engine):
    with pytest.raises(ProviderConfigurationError) as info:
        await run_ocr(
            PNG_1PX,
            client=idle_client(),
            environ=env(**{"OCR_SPACE_ENGINE": engine}),
        )
    assert "OCR_SPACE_ENGINE" in str(info.value)


async def test_blank_engine_falls_back_to_the_documented_default():
    client, seen = capturing_client(lambda request: json_response(space_body()))
    await run_ocr(PNG_1PX, client=client, environ=env(**{"OCR_SPACE_ENGINE": ""}))
    assert multipart_parts(seen["request"])["OCREngine"] == b"2"


async def test_endpoint_can_be_overridden_but_not_to_a_non_http_url():
    client, seen = capturing_client(lambda request: json_response(space_body()))
    await run_ocr(PNG_1PX, client=client, environ=env(**{ENV_SPACE_URL: "https://ocr.internal/parse"}))
    assert str(seen["request"].url) == "https://ocr.internal/parse"

    with pytest.raises(ProviderConfigurationError) as info:
        await run_ocr(
            PNG_1PX,
            client=idle_client(),
            environ=env(**{ENV_SPACE_URL: "ftp://ocr.internal/parse"}),
        )
    assert ENV_SPACE_URL in str(info.value)


@pytest.mark.parametrize("value", ["abc", "0"])
async def test_invalid_file_limit_is_a_configuration_error(value):
    with pytest.raises(ProviderConfigurationError) as info:
        await run_ocr(
            PNG_1PX,
            client=idle_client(),
            environ=env(**{"OCR_SPACE_MAX_BYTES": value}),
        )
    assert "OCR_SPACE_MAX_BYTES" in str(info.value)


# --------------------------------------------------------------------------- responses


async def test_successful_text_is_returned():
    client, _ = capturing_client(lambda request: json_response(space_body("QUTUB MINAR\nDelhi")))
    result = await run_ocr(PNG_1PX, client=client, environ=env())
    assert result.text == "QUTUB MINAR\nDelhi"
    assert not result.is_empty


async def test_empty_text_is_a_successful_no_text_answer():
    client, _ = capturing_client(lambda request: json_response(space_body("")))
    result = await run_ocr(PNG_1PX, client=client, environ=env())
    assert result.text == ""
    assert result.is_empty  # valid: the image simply had no readable text


async def test_provider_error_inside_http_200_is_an_authentication_failure():
    client, _ = capturing_client(
        lambda request: json_response(
            space_body(
                None,
                errored=True,
                message="Invalid or missing API key.",
                include_results=False,
            )
        )
    )
    with pytest.raises(ProviderAuthenticationError) as info:
        await run_ocr(PNG_1PX, client=client, environ=env())
    assert ENV_SPACE_KEY in str(info.value)
    assert TEST_KEY not in str(info.value)  # the key never reaches a message


async def test_provider_quota_message_becomes_a_rate_limit_error():
    client, _ = capturing_client(
        lambda request: json_response(
            space_body(None, errored=True, message="Your plan has reached the daily limit of 500 requests.", include_results=False)
        )
    )
    with pytest.raises(ProviderRateLimitError):
        await run_ocr(PNG_1PX, client=client, environ=env())


async def test_provider_timeout_message_becomes_a_timeout_error():
    client, _ = capturing_client(
        lambda request: json_response(
            space_body(None, errored=True, message="The operation timed out while processing the image.", include_results=False)
        )
    )
    with pytest.raises(ProviderTimeoutError):
        await run_ocr(PNG_1PX, client=client, environ=env())


async def test_unknown_provider_error_stays_a_truthful_provider_error():
    client, _ = capturing_client(
        lambda request: json_response(
            space_body(None, errored=True, message="Unsupported image layout.", include_results=False)
        )
    )
    with pytest.raises(ProviderError) as info:
        await run_ocr(PNG_1PX, client=client, environ=env())
    assert "Unsupported image layout." in str(info.value)


async def test_entry_level_error_is_reported_instead_of_empty_text():
    client, _ = capturing_client(
        lambda request: json_response(
            {
                "ParsedResults": [
                    {"FileParseExitCode": -30, "ParsedText": "", "ErrorMessage": "Validation error: file is not an image."}
                ],
                "OCRExitCode": 3,
                "IsErroredOnProcessing": False,
                "ErrorMessage": None,
            }
        )
    )
    with pytest.raises(ProviderError) as info:
        await run_ocr(PNG_1PX, client=client, environ=env())
    assert "Validation error" in str(info.value)


async def test_exit_code_3_without_text_is_a_failure_not_an_empty_result():
    client, _ = capturing_client(lambda request: json_response(space_body(None, exit_code=3)))
    with pytest.raises(ProviderError) as info:
        await run_ocr(PNG_1PX, client=client, environ=env())
    assert "OCRExitCode 3" in str(info.value)


async def test_malformed_json_is_a_response_error_not_empty_text():
    client, _ = capturing_client(lambda request: httpx.Response(200, text="<html>nope</html>"))
    with pytest.raises(ProviderResponseError):
        await run_ocr(PNG_1PX, client=client, environ=env())


async def test_json_without_results_is_a_response_error():
    client, _ = capturing_client(lambda request: json_response({"OCRExitCode": 1}))
    with pytest.raises(ProviderResponseError) as info:
        await run_ocr(PNG_1PX, client=client, environ=env())
    assert "no OCR results" in str(info.value)


@pytest.mark.parametrize("status", [401, 403])
async def test_http_auth_failure_is_classified(status):
    client, _ = capturing_client(lambda request: httpx.Response(status))
    with pytest.raises(ProviderAuthenticationError):
        await run_ocr(PNG_1PX, client=client, environ=env())


async def test_http_429_with_retry_after_is_a_rate_limit_error():
    client, _ = capturing_client(
        lambda request: httpx.Response(429, headers={"Retry-After": "7"})
    )
    with pytest.raises(ProviderRateLimitError) as info:
        await run_ocr(PNG_1PX, client=client, environ=env())
    assert info.value.retry_after == 7.0


async def test_http_500_is_a_server_error():
    client, _ = capturing_client(lambda request: httpx.Response(500))
    with pytest.raises(ProviderServerError):
        await run_ocr(PNG_1PX, client=client, environ=env())


async def test_transport_timeout_is_a_timeout_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderTimeoutError):
        await run_ocr(PNG_1PX, client=client, environ=env())


async def test_connection_failure_is_a_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderConnectionError):
        await run_ocr(PNG_1PX, client=client, environ=env())


async def test_empty_upload_is_rejected_before_any_request():
    with pytest.raises(ValueError):
        await run_ocr(b"", client=httpx.AsyncClient(), environ=env())


# --------------------------------------------------------------------------- size/format


async def test_small_supported_image_is_forwarded_untouched():
    original = png(64, 48)
    client, seen = capturing_client(lambda request: json_response(space_body()))
    await run_ocr(
        original,
        filename="photo.png",
        content_type="image/png",
        client=client,
        environ=env(),
    )
    parts = multipart_parts(seen["request"])
    assert parts["file"] == original
    assert b'name="file"' in seen["request"].content and b"photo.png" in seen["request"].content


async def test_oversized_image_is_recompressed_under_the_limit():
    original = noisy_png()
    limit = 700_000
    assert len(original) > limit  # genuinely over before the adapter runs

    client, seen = capturing_client(lambda request: json_response(space_body()))
    await run_ocr(
        original,
        filename="panorama.png",
        content_type="image/png",
        client=client,
        environ=env(**{"OCR_SPACE_MAX_BYTES": str(limit)}),
    )

    uploaded = multipart_parts(seen["request"])["file"]
    assert len(uploaded) <= limit
    assert uploaded.startswith(b"\xff\xd8")  # JPEG with its magic bytes
    # Still a real, decodable image — text legibility is not thrown away.
    assert Image.open(io.BytesIO(uploaded)).size[0] > 500


async def test_unsupported_format_is_transcoded_to_a_documented_one():
    webp = webp_bytes()
    assert len(webp) < DEFAULT_SPACE_MAX_BYTES  # size is not the issue: format is

    client, seen = capturing_client(lambda request: json_response(space_body()))
    await run_ocr(
        webp,
        filename="scan.webp",
        content_type="image/webp",
        client=client,
        environ=env(),
    )

    uploaded = multipart_parts(seen["request"])["file"]
    assert uploaded.startswith(b"\x89PNG")  # lossless transcode
    assert b"scan.png" in seen["request"].content  # extension follows the bytes


async def test_undecodable_image_fails_before_the_request_with_a_clear_message():
    client, seen = capturing_client(lambda request: json_response(space_body()))
    with pytest.raises(ProviderConfigurationError) as info:
        await run_ocr(
            b"not an image at all",
            filename="broken.png",
            content_type="image/webp",
            client=client,
            environ=env(),
        )
    assert "could not read the uploaded image" in str(info.value)
    assert "request" not in seen


async def test_image_that_cannot_fit_is_not_sent_at_all():
    client, seen = capturing_client(lambda request: json_response(space_body()))
    with pytest.raises(ProviderConfigurationError) as info:
        await run_ocr(
            noisy_png(800, 800),
            filename="huge.png",
            content_type="image/png",
            client=client,
            environ=env(**{"OCR_SPACE_MAX_BYTES": "1024"}),
        )
    message = str(info.value)
    assert "1024 byte file limit" in message
    assert "OCR_SPACE_MAX_BYTES" in message
    assert "request" not in seen


# --------------------------------------------------------------------------- pipeline wiring


async def test_extraction_uses_ocr_space_and_flags_the_provider(monkeypatch):
    """The real extraction picks the configured provider up, with no skip."""
    from app.services.extraction_service import run_extraction
    from app.services.image_service import prepare_image

    monkeypatch.setenv("OCR_PROVIDER", PROVIDER_OCR_SPACE)
    monkeypatch.setenv(ENV_SPACE_KEY, TEST_KEY)
    monkeypatch.setenv("VISION_API_BASE_URL", "https://vision.example.test/v1")
    monkeypatch.setenv("VISION_API_KEY", "sk-test-vision")
    monkeypatch.setenv("VISION_MODEL", "vision-model-x")

    ocr_client, _ = capturing_client(lambda request: json_response(space_body("1843 RECORDS OFFICE")))

    vision_client = _vision_client({
        "interpretation": "A stone plaque.",
        "text": "",
        "entities": [{"name": "Records Office", "type": "place", "confidence": 0.9}],
        "relationships": [],
    })
    prepared = prepare_image(plaque_image(), filename="plaque.png", content_type="image/png")

    result = await run_extraction(
        prepared,
        filename="plaque.png",
        ocr_client=ocr_client,
        vision_client=vision_client,
    )

    assert result.extracted_text == "1843 RECORDS OFFICE"
    assert result.ocr_attempted is True
    assert result.ocr_provider == PROVIDER_OCR_SPACE
    assert not any(w.startswith("OCR skipped") for w in result.warnings)


async def test_extraction_without_the_key_warns_with_the_right_variable(monkeypatch):
    """The reported warning now names OCR.space's variable, not the legacy one."""
    from app.services.extraction_service import run_extraction
    from app.services.image_service import prepare_image

    monkeypatch.setenv("OCR_PROVIDER", PROVIDER_OCR_SPACE)
    monkeypatch.delenv(ENV_SPACE_KEY, raising=False)
    monkeypatch.setenv("VISION_API_BASE_URL", "https://vision.example.test/v1")
    monkeypatch.setenv("VISION_API_KEY", "sk-test-vision")
    monkeypatch.setenv("VISION_MODEL", "vision-model-x")

    async def vision_handler(request: httpx.Request) -> httpx.Response:
        return _vision_response({
            "interpretation": "A plaque.",
            "text": "vision text",
            "entities": [{"name": "Records Office", "type": "place", "confidence": 0.9}],
            "relationships": [],
        })

    vision_client = httpx.AsyncClient(transport=httpx.MockTransport(vision_handler))
    prepared = prepare_image(plaque_image(), filename="plaque.png", content_type="image/png")

    result = await run_extraction(prepared, filename="plaque.png", vision_client=vision_client)

    skipped = [w for w in result.warnings if w.startswith("OCR skipped")]
    assert skipped and ENV_SPACE_KEY in skipped[0]
    assert result.ocr_attempted is False
    # The run still produced a result from the vision channel.
    assert result.extracted_text == "vision text"


def test_processing_configuration_gate_understands_ocr_space(monkeypatch):
    from app.services.analysis_service import processing_configuration_error

    for name in (
        "VISION_API_BASE_URL",
        "VISION_API_KEY",
        "VISION_MODEL",
        "OCR_PROVIDER",
        ENV_SPACE_KEY,
        ENV_SPACE_URL,
        "OCR_API_URL",
        "OCR_API_KEY",
        "HG_DEMO_MODE",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("VISION_API_BASE_URL", "https://vision.example.test/v1")
    monkeypatch.setenv("VISION_API_KEY", "sk-test-vision")
    monkeypatch.setenv("VISION_MODEL", "vision-model-x")

    # Fully configured OCR.space: no configuration problem at all.
    monkeypatch.setenv("OCR_PROVIDER", PROVIDER_OCR_SPACE)
    monkeypatch.setenv(ENV_SPACE_KEY, TEST_KEY)
    assert processing_configuration_error() is None

    # A URL without its key is a typo, and the message names the key.
    monkeypatch.delenv(ENV_SPACE_KEY, raising=True)
    monkeypatch.setenv(ENV_SPACE_URL, DEFAULT_SPACE_URL)
    message = processing_configuration_error()
    assert message and ENV_SPACE_KEY in message and "partially" in message

    # An unknown provider value is rejected with its own name.
    monkeypatch.delenv(ENV_SPACE_URL, raising=True)
    monkeypatch.setenv("OCR_PROVIDER", "tesseract-cloud")
    message = processing_configuration_error()
    assert message and "tesseract-cloud" in message


def test_diagnostics_report_the_selected_provider_without_leaking_the_key():
    from app.api.routes.diagnostics import _ocr_status

    status = _ocr_status(
        {"OCR_PROVIDER": PROVIDER_OCR_SPACE, ENV_SPACE_KEY: TEST_KEY}
    )
    assert status["provider"] == PROVIDER_OCR_SPACE
    assert status["configured"] is True
    assert status["endpoint_host"] == "api.ocr.space"
    assert status["credentials"]["api_key"] is True
    assert TEST_KEY not in json.dumps(status)

    missing = _ocr_status({"OCR_PROVIDER": PROVIDER_OCR_SPACE})
    assert missing["configured"] is False
    assert ENV_SPACE_KEY in (missing["problem"] or "")


# --------------------------------------------------------------------------- live call


@pytest.mark.live
async def test_live_ocr_space_round_trip():
    """A REAL OCR.space request over a generated plaque image.

    Skipped unless ``HERITAGE_LIVE_PROVIDER_TESTS=1`` and
    ``OCR_SPACE_API_KEY`` are set; asserts only what the provider
    legitimately guarantees: text from *this* image, provenance ``live``.
    """
    if os.environ.get("HERITAGE_LIVE_PROVIDER_TESTS") != "1":
        pytest.skip("live provider tests disabled (set HERITAGE_LIVE_PROVIDER_TESTS=1)")
    if not (os.environ.get(ENV_SPACE_KEY) or "").strip():
        pytest.skip(f"live OCR.space not configured (missing {ENV_SPACE_KEY})")

    result = await run_ocr(
        plaque_image(),
        filename="plaque.png",
        content_type="image/png",
    )

    assert result.provenance == "live"
    assert result.provider == PROVIDER_OCR_SPACE
    # The recognised text must correspond to the image that was sent.
    assert "HERITAGEGRAPH AI" in result.text
    assert "1843" in result.text
    assert "RECORDS OFFICE" in result.text
