"""Google Cloud Vision OCR adapter — mocked provider tests.

These are **mocked** provider tests: the HTTP transport is an
``httpx.MockTransport``. They verify request construction, authentication
selection, response parsing (including the legitimate empty result) and
error classification. No live Google call is made here — that requires
credentials this workspace does not have (see README → Known limitations).
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderResponseError,
)
from app.integrations.google_vision import (
    credentials_configured,
    extract_text,
    run_google_vision,
)
from app.integrations.ocr import (
    PROVIDER_GOOGLE,
    PROVIDER_HTTP,
    configuration_problem,
    run_ocr,
    selected_provider,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"payload"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _annotate(text: str | None, *, languages=("en",)) -> dict:
    response: dict = {}
    if text is not None:
        response["fullTextAnnotation"] = {
            "text": text,
            "pages": [
                {
                    "blocks": [
                        {
                            "property": {
                                "detectedLanguages": [
                                    {"languageCode": code} for code in languages
                                ]
                            }
                        }
                    ]
                }
            ],
        }
        response["textAnnotations"] = [{"description": text}]
    return {"responses": [response]}


@pytest.mark.anyio
async def test_google_vision_sends_document_text_detection_with_api_key():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_annotate("QUTUB MINAR\nDELHI 1193"))

    async with _client(handler) as client:
        text, languages = await run_google_vision(
            PNG, client=client, environ={"GOOGLE_VISION_API_KEY": "test-key"}
        )

    assert text == "QUTUB MINAR\nDELHI 1193"
    assert languages == ["en"]
    assert captured["url"].startswith("https://vision.googleapis.com/v1/images:annotate")
    assert captured["params"]["key"] == "test-key"
    # No bearer header when an API key is used.
    assert captured["auth"] is None
    # The documented dense-text feature, with the real image bytes base64ed.
    feature = captured["body"]["requests"][0]["features"][0]
    assert feature["type"] == "DOCUMENT_TEXT_DETECTION"
    assert captured["body"]["requests"][0]["image"]["content"]


@pytest.mark.anyio
async def test_google_vision_prefers_access_token_and_never_leaks_it():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_annotate(None))

    async with _client(handler) as client:
        await run_google_vision(
            PNG, client=client, environ={"GOOGLE_VISION_ACCESS_TOKEN": "secret-token"}
        )

    assert captured["auth"] == "Bearer secret-token"


@pytest.mark.anyio
async def test_google_vision_empty_result_is_a_valid_answer_not_an_error():
    """An image with no readable text must yield '', never fabricated text."""
    async with _client(lambda request: httpx.Response(200, json=_annotate(None))) as client:
        text, languages = await run_google_vision(
            PNG, client=client, environ={"GOOGLE_VISION_API_KEY": "k"}
        )

    assert text == ""
    assert languages == []


def test_google_vision_falls_back_to_flat_text_annotations():
    payload = {"responses": [{"textAnnotations": [{"description": "PLAQUE TEXT"}]}]}
    assert extract_text(payload) == "PLAQUE TEXT"


@pytest.mark.anyio
async def test_google_vision_per_image_permission_error_is_classified():
    """Cloud Vision reports per-image failures as HTTP 200 + responses[0].error."""
    payload = {
        "responses": [
            {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "denied"}}
        ]
    }
    async with _client(lambda request: httpx.Response(200, json=payload)) as client:
        with pytest.raises(ProviderAuthenticationError) as info:
            await run_google_vision(
                PNG, client=client, environ={"GOOGLE_VISION_API_KEY": "k"}
            )
    assert "test" not in str(info.value) or "test-key" not in str(info.value)
    assert "PERMISSION" not in str(info.value)  # safe, human message only


@pytest.mark.anyio
async def test_google_vision_http_429_and_5xx_are_classified():
    from app.integrations import ProviderRateLimitError, ProviderServerError

    async with _client(
        lambda request: httpx.Response(429, headers={"Retry-After": "7"})
    ) as client:
        with pytest.raises(ProviderRateLimitError):
            await run_google_vision(
                PNG, client=client, environ={"GOOGLE_VISION_API_KEY": "k"}
            )

    async with _client(lambda request: httpx.Response(503)) as client:
        with pytest.raises(ProviderServerError):
            await run_google_vision(
                PNG, client=client, environ={"GOOGLE_VISION_API_KEY": "k"}
            )


@pytest.mark.anyio
async def test_google_vision_malformed_payload_fails_loudly():
    async with _client(lambda request: httpx.Response(200, json={"nope": True})) as client:
        with pytest.raises(ProviderResponseError):
            await run_google_vision(
                PNG, client=client, environ={"GOOGLE_VISION_API_KEY": "k"}
            )


def test_unconfigured_ocr_warning_mentions_google_vision(monkeypatch):
    """The run's warning must name a real configuration path (this was the
    user-visible symptom: a warning that only mentioned OCR_API_URL/KEY)."""
    for name in (
        "OCR_API_URL",
        "OCR_API_KEY",
        "OCR_PROVIDER",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_VISION_API_KEY",
        "GOOGLE_VISION_ACCESS_TOKEN",
        "GOOGLE_CLOUD_PROJECT",
        "K_SERVICE",
        "GOOGLE_GHA_CREDS_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    problem = configuration_problem({})
    assert problem is not None
    assert "OCR_PROVIDER=google_vision" in problem


def test_missing_google_credentials_names_the_right_variables(monkeypatch):
    for name in (
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_VISION_API_KEY",
        "GOOGLE_VISION_ACCESS_TOKEN",
        "GOOGLE_CLOUD_PROJECT",
        "K_SERVICE",
        "GOOGLE_GHA_CREDS_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    problem = configuration_problem({"OCR_PROVIDER": "google_vision"})
    assert problem is not None
    assert "GOOGLE_APPLICATION_CREDENTIALS" in problem
    assert "GOOGLE_VISION_API_KEY" in problem
    # Google never asks for the generic HTTP adapter's variables.
    assert "OCR_API_URL" not in problem
    assert not credentials_configured({})


def test_provider_selection_defaults_to_http_and_validates():
    assert selected_provider({}) == PROVIDER_HTTP
    assert selected_provider({"OCR_PROVIDER": "GOOGLE_VISION"}) == PROVIDER_GOOGLE
    with pytest.raises(ProviderConfigurationError):
        selected_provider({"OCR_PROVIDER": "tesseract-cloud"})


@pytest.mark.anyio
async def test_run_ocr_dispatches_to_google_vision_and_tags_provenance():
    async with _client(
        lambda request: httpx.Response(200, json=_annotate("INSCRIPTION"))
    ) as client:
        result = await run_ocr(
            PNG,
            client=client,
            environ={
                "OCR_PROVIDER": "google_vision",
                "GOOGLE_VISION_API_KEY": "k",
            },
        )

    assert result.text == "INSCRIPTION"
    assert result.provider == PROVIDER_GOOGLE
    assert result.provenance == "injected-client"
    assert result.engine == "vision.googleapis.com"


@pytest.mark.anyio
async def test_http_provider_still_requires_its_own_variables():
    """The generic adapter keeps its documented contract, and its message
    now also points at the Google Cloud Vision alternative."""
    with pytest.raises(ProviderConfigurationError) as info:
        await run_ocr(PNG, environ={"OCR_PROVIDER": "http"})
    message = str(info.value)
    assert "OCR_API_URL" in message
    assert "OCR_PROVIDER=google_vision" in message
