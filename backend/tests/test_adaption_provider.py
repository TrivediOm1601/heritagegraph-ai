"""Adaption provider adapter — configuration, error taxonomy, parsing.

Everything here is **mocked** (``httpx.MockTransport``); no request leaves
the process. The live behaviour of this key was verified separately and is
recorded in the module docstring of :mod:`app.integrations.adaption`: the
credentials authenticate, but the hosted inference catalog is empty, so
``POST /responses`` answers 404 for every model id. That is exactly the
case :class:`AdaptionModelUnavailableError` exists for, and it is pinned
here so the app keeps reporting it honestly if it happens again.
"""

from __future__ import annotations

import httpx
import pytest

from app.integrations import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderServerError,
    ProviderTimeoutError,
)
from app.integrations.adaption import (
    AdaptionConfig,
    AdaptionModelUnavailableError,
    create_response,
    extract_text,
    list_hosted_models,
    load_config,
    synthesis_enabled,
)

pytestmark = pytest.mark.anyio

ENV = {
    "ADAPTION_API_KEY": "pt_live_test",
    "ADAPTION_MODEL": "hosted-model-1",
}

CONFIG = AdaptionConfig(
    base_url="https://api.prod.adaptionlabs.ai/api/v1",
    api_key="pt_live_test",
    model="hosted-model-1",
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_missing_configuration_names_the_variables():
    with pytest.raises(ProviderConfigurationError) as info:
        load_config({})

    message = str(info.value)
    assert "ADAPTION_API_KEY" in message
    assert "ADAPTION_MODEL" in message


def test_configuration_uses_the_documented_default_host():
    config = load_config(ENV)

    assert config.base_url == "https://api.prod.adaptionlabs.ai/api/v1"
    assert config.model == "hosted-model-1"


def test_base_url_override_is_honoured_without_a_trailing_slash():
    config = load_config({**ENV, "ADAPTION_API_BASE_URL": "https://example.test/api/v1/"})

    assert config.base_url == "https://example.test/api/v1"


def test_synthesis_is_off_unless_explicitly_enabled():
    assert synthesis_enabled({}) is False
    assert synthesis_enabled({"HG_SYNTHESIS": "0"}) is False
    assert synthesis_enabled({"HG_SYNTHESIS": "true"}) is True
    assert synthesis_enabled({"HG_SYNTHESIS": "1"}) is True


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


async def test_request_uses_bearer_auth_and_the_responses_endpoint():
    import json

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"output_text": "ok"})

    text = await create_response("hello", config=CONFIG, client=_client(handler))

    assert text == "ok"
    assert seen["url"] == "https://api.prod.adaptionlabs.ai/api/v1/responses"
    assert seen["auth"] == "Bearer pt_live_test"
    assert seen["body"]["model"] == "hosted-model-1"
    assert seen["body"]["input"] == [{"role": "user", "content": "hello"}]


async def test_instructions_and_token_cap_are_sent():
    import json

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"output_text": "ok"})

    await create_response(
        "hello",
        instructions="Only use [1].",
        max_output_tokens=128,
        config=CONFIG,
        client=_client(handler),
    )

    assert seen["body"]["instructions"] == "Only use [1]."
    assert seen["body"]["max_output_tokens"] == 128


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


async def test_bad_credentials_are_reported_as_authentication_failures():
    client = _client(lambda request: httpx.Response(401, json={"error": "nope"}))

    with pytest.raises(ProviderAuthenticationError):
        await create_response("hi", config=CONFIG, client=client)


async def test_model_not_found_is_its_own_error_and_names_the_model():
    client = _client(
        lambda request: httpx.Response(
            404, json={"error": {"message": "Model not found", "type": "invalid_request_error"}}
        )
    )

    with pytest.raises(AdaptionModelUnavailableError) as info:
        await create_response("hi", config=CONFIG, client=client)

    message = str(info.value)
    assert "hosted-model-1" in message
    # The message tells the operator how to fix it, and names no secret.
    assert "ADAPTION_MODEL" in message
    assert "pt_live_test" not in message


async def test_rate_limit_and_server_errors_are_distinguished():
    with pytest.raises(ProviderRateLimitError):
        await create_response(
            "hi",
            config=CONFIG,
            client=_client(lambda request: httpx.Response(429, json={"error": "slow down"})),
        )

    with pytest.raises(ProviderServerError):
        await create_response(
            "hi",
            config=CONFIG,
            client=_client(lambda request: httpx.Response(500, json={"error": "boom"})),
        )


async def test_timeout_and_transport_failures_are_distinguished():
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    with pytest.raises(ProviderTimeoutError):
        await create_response("hi", config=CONFIG, client=_client(timeout_handler))

    def connect_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(ProviderConnectionError):
        await create_response("hi", config=CONFIG, client=_client(connect_handler))


async def test_unusable_body_is_never_an_empty_success():
    client = _client(lambda request: httpx.Response(200, json={"unexpected": "shape"}))

    with pytest.raises(ProviderResponseError):
        await create_response("hi", config=CONFIG, client=client)


# ---------------------------------------------------------------------------
# Response parsing (undocumented envelope → defensive carriers)
# ---------------------------------------------------------------------------


def test_extract_text_reads_every_plausible_carrier():
    assert extract_text({"output_text": "direct"}) == "direct"
    assert (
        extract_text(
            {
                "output": [
                    {"content": [{"type": "output_text", "text": "part one"}]},
                    {"content": [{"type": "output_text", "text": "part two"}]},
                ]
            }
        )
        == "part one\npart two"
    )
    assert (
        extract_text({"choices": [{"message": {"content": "openai style"}}]})
        == "openai style"
    )
    assert extract_text({"output_text": "   "}) is None
    assert extract_text({"foo": "bar"}) is None
    assert extract_text("not a dict") is None


# ---------------------------------------------------------------------------
# Hosted model catalog
# ---------------------------------------------------------------------------


async def test_hosted_catalog_lists_ids_and_asks_for_hosted_models():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200, json={"object": "list", "data": [{"id": "hosted-a"}, {"id": "hosted-b"}]}
        )

    models = await list_hosted_models(config=CONFIG, client=_client(handler))

    assert models == ["hosted-a", "hosted-b"]
    assert "type=hosted" in seen["url"]


async def test_empty_hosted_catalog_is_reported_as_empty_not_an_error():
    """The verified state of this deployment's key right now."""

    client = _client(lambda request: httpx.Response(200, json={"object": "list", "data": []}))

    assert await list_hosted_models(config=CONFIG, client=client) == []


async def test_catalog_with_an_unexpected_body_is_empty_not_invented():
    client = _client(lambda request: httpx.Response(200, json={"models": ["x"]}))

    assert await list_hosted_models(config=CONFIG, client=client) == []
