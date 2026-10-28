"""Live-mode (non-demo) analysis pipeline over a real HTTP provider.

This module closes the gap left by the fixture-based suites: every other
processing test injects ``httpx.MockTransport`` clients or runs
``HG_DEMO_MODE=1``, so neither proved that **live mode** — the code path a
configured deployment actually takes — sends the *uploaded image bytes* to
the *configured endpoint* and uses only that response.

Here a tiny asyncio HTTP server listens on ``127.0.0.1`` and speaks just
enough HTTP to stand in for an OpenAI-compatible vision endpoint. The
pipeline runs through its real code path with real configuration
(``VISION_API_BASE_URL`` pointing at the loopback server), a real
Alembic-migrated database and **no** ``HG_DEMO_MODE``:

* the recorded request is inspected byte-for-byte: bearer auth, model
  id, and the base64 ``data:`` URL decoding to exactly the prepared PNG
  — proving the upload reaches the provider (not a filename, not a
  fixture);
* the run completes with the stub's own payload, which is deliberately
  different from ``analysis_service._DEMO_VISION_PAYLOAD`` — so a
  fixture substitution would be caught;
* no ``DEMO MODE`` warning may appear in a live run;
* a provider auth failure leaves the run ``failed``, never a fabricated
  success.

Zero external network: the "provider" is loopback only.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from app.core.config import reset_settings_cache
from app.db.session import reset_default_engine

pytestmark = pytest.mark.anyio

#: Deliberately distinct from the demo fixture payload: if any fixture
#: leaked into this live run, these names would not appear.
LIVE_ENTITY_NAMES = ["Rani ki Vav", "Patan", "Stepwell"]
LIVE_INTERPRETATION = (
    "LIVE STUB — a stepwell with sculpted galleries; candidate "
    "identification: Rani ki Vav, Patan, Gujarat (uncertain)."
)
LIVE_TEXT = "Rani ki Vav - Queen's Stepwell - Patan, Gujarat"

VISION_PAYLOAD = {
    "text": LIVE_TEXT,
    "interpretation": LIVE_INTERPRETATION,
    "entities": [
        {"name": "Rani ki Vav", "type": "place", "confidence": 0.93},
        {"name": "Patan", "type": "place", "confidence": 0.81},
        {"name": "Stepwell", "type": "other", "confidence": 0.7},
    ],
    "relationships": [
        {"source": "Rani ki Vav", "target": "Patan", "label": "located in",
         "confidence": 0.9},
    ],
}


@dataclass
class StubVisionServer:
    """Minimal loopback HTTP server that records every request body."""

    status: int = 200
    payload: Optional[dict[str, Any]] = None
    bodies: list[bytes] = field(default_factory=list)
    authorization: Optional[str] = None
    server: Optional[asyncio.AbstractServer] = None
    port: Optional[int] = None

    @property
    def base_url(self) -> str:
        assert self.port is not None
        return f"http://127.0.0.1:{self.port}/v1"

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            headers = head.decode("latin-1").split("\r\n")
            length = 0
            for line in headers[1:]:
                name, _, value = line.partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value.strip())
            body = await reader.readexactly(length) if length else b""
            self.bodies.append(body)
            for line in headers[1:]:
                name, _, value = line.partition(":")
                if name.strip().lower() == "authorization":
                    self.authorization = value.strip()

            if self.status == 200:
                content = json.dumps(
                    {
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        self.payload or VISION_PAYLOAD
                                    ),
                                },
                            }
                        ]
                    }
                ).encode()
            else:
                content = json.dumps(
                    {"error": {"message": "stub provider error"}}
                ).encode()

            writer.write(
                b"HTTP/1.1 "
                + str(self.status).encode()
                + b" Stub\r\ncontent-type: application/json\r\ncontent-length: "
                + str(len(content)).encode()
                + b"\r\nconnection: close\r\n\r\n"
                + content
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    async def __aenter__(self) -> "StubVisionServer":
        self.server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0
        )
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_exc: object) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()


@pytest.fixture
def live_env(monkeypatch, tmp_path):
    """Real (non-demo) provider + database configuration for the pipeline.

    Mirrors the deployment shape: ``DATABASE_URL`` migrated and seeded,
    all provider variables set, ``HG_DEMO_MODE`` explicitly absent.
    Yields a setter so a test can point ``VISION_API_BASE_URL`` at its
    own stub server after that server is bound.
    """
    from conftest import _provision_live_database

    db_url = f"sqlite:///{tmp_path / 'live-mode.db'}"
    _provision_live_database(db_url)

    monkeypatch.setenv("DATABASE_URL", db_url)
    for name in (
        "HG_DEMO_MODE",
        "HG_LIVE_SOURCES",
        "OCR_API_URL",
        "OCR_API_KEY",
        "VISION_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VISION_API_KEY", "stub-vision-key")
    monkeypatch.setenv("VISION_MODEL", "stub-vision-model")

    def _point_at(base_url: str) -> None:
        monkeypatch.setenv("VISION_API_BASE_URL", base_url)

    reset_settings_cache()
    reset_default_engine()
    try:
        yield _point_at
    finally:
        reset_settings_cache()
        reset_default_engine()


def _sample_png() -> bytes:
    """A real, decodable PNG (same gate the upload route enforces)."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (214, 188, 140)).save(buffer, format="PNG")
    return buffer.getvalue()


async def test_live_mode_sends_the_uploaded_bytes_and_uses_that_response(
    live_env,
):
    """The provider receives the actual image; its answer is the result."""
    from app.services.analysis_service import DEMO_WARNING, run_analysis

    image = _sample_png()
    async with StubVisionServer() as stub:
        live_env(stub.base_url)
        response = await run_analysis(image, "stepwell.png", "image/png")

    # 1. The run completed through live mode — no fixture label anywhere.
    assert response.status == "completed", response.error
    assert not [
        w for w in (response.warnings or []) if "DEMO MODE" in w
    ], response.warnings
    assert DEMO_WARNING not in (response.warnings or [])

    # 2. The result is the provider's, not the bundled fixture's.
    assert response.extracted_text == LIVE_TEXT
    assert response.interpretation == LIVE_INTERPRETATION
    # The read side orders rows by entity name, so compare as a set.
    assert {entity.name for entity in response.entities} == set(LIVE_ENTITY_NAMES)
    fixture_names = {"Ada Lovelace", "Charles Babbage", "Analytical Engine"}
    assert fixture_names.isdisjoint({e.name for e in response.entities})

    # 3. Exactly one vision request, authenticated with the configured key
    #    and model, carrying the image bytes we uploaded.
    assert len(stub.bodies) == 1
    assert stub.authorization == "Bearer stub-vision-key"
    request = json.loads(stub.bodies[0])
    assert request["model"] == "stub-vision-model"
    content = request["messages"][0]["content"]
    image_part = next(
        part for part in content if part.get("type") == "image_url"
    )
    data_url = image_part["image_url"]["url"]
    prefix, _, encoded = data_url.partition(",")
    assert prefix == "data:image/png;base64"
    from app.services.image_service import prepare_image

    prepared = prepare_image(image, filename="stepwell.png", content_type="image/png")
    assert base64.b64decode(encoded) == prepared.to_png_bytes()

    # 4. OCR is unconfigured: the run says so instead of inventing text.
    assert any("OCR" in w for w in response.warnings or []), response.warnings

    # 5. The persisted run reads back identically (right analysis id).
    from app.db.session import session_scope
    from app.repositories.analysis_repository import get_analysis_run

    async with session_scope() as session:
        run = await get_analysis_run(session, response.id)
        assert run is not None
        assert run.status == "completed"
        assert run.interpretation == LIVE_INTERPRETATION


async def test_concurrent_analyses_persist_the_same_entities(live_env):
    """Two overlapping uploads extracting the same entity must BOTH finish.

    Regression guard for the intermittent E2E failure: ``upsert_entities``
    used a find-then-insert sequence against ``uq_entities_name_type``, so
    two pipelines extracting "Rani ki Vav" at the same time could race and
    one run died with an integrity error.
    """
    from app.services.analysis_service import run_analysis

    async with StubVisionServer() as stub:
        live_env(stub.base_url)
        first, second = await asyncio.gather(
            run_analysis(_sample_png(), "a.png", "image/png"),
            run_analysis(_sample_png(), "b.png", "image/png"),
        )

    assert first.status == "completed", first.error
    assert second.status == "completed", second.error
    assert {e.name for e in first.entities} == set(LIVE_ENTITY_NAMES)
    assert {e.name for e in second.entities} == set(LIVE_ENTITY_NAMES)
    assert first.id != second.id


async def test_live_mode_provider_auth_failure_fails_the_run(live_env):
    """A 401 must surface as a failed run — never fixture fallback data."""
    from app.services.analysis_service import run_analysis

    async with StubVisionServer(status=401) as stub:
        live_env(stub.base_url)
        response = await run_analysis(_sample_png(), "rejected.png", "image/png")

    assert response.status == "failed"
    assert response.error, "a failed run must explain itself"
    assert "DEMO MODE" not in " ".join(response.warnings or [])
    # No fabricated extraction on failure.
    assert response.entities in (None, [])
    assert response.extracted_text in (None, "")


async def test_live_mode_unreachable_provider_fails_the_run(live_env):
    """Connection refused → honest failure, not a silent fixture result."""
    from app.services.analysis_service import run_analysis

    # Bind and immediately release a port so nothing is listening on it.
    async def _closed_port() -> int:
        server = await asyncio.start_server(
            lambda r, w: w.close(), "127.0.0.1", 0
        )
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        return port

    live_env(f"http://127.0.0.1:{await _closed_port()}/v1")
    response = await run_analysis(_sample_png(), "offline.png", "image/png")

    assert response.status == "failed"
    assert response.error
    assert "DEMO MODE" not in " ".join(response.warnings or [])