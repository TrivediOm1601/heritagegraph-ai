"""B5 — upload validation (the "invalid upload" + contract half of the flow).

Built on the shared ``upload`` / ``sample_image`` fixtures from
conftest.py. Covers: the request shape the frontend actually sends, every
rejection path (unsupported type, wrong field, empty, oversize, undecodable
content), and the validated-acceptance path.

With no provider credentials configured (this workspace — INT-005) and
demo mode off, a fully valid upload is refused with the canonical 503
naming the missing provider variables *before* a run is queued — the
case exercised here. With ``DATABASE_URL`` + ``HG_DEMO_MODE`` (or real
credentials) the same request answers 202; the full queue → process →
complete journey runs in ``test_api.py`` against the live server, and
the orchestration itself in ``test_analysis_service.py``.
"""

import io

from app.api.routes import analysis as analysis_routes


def test_valid_upload_is_refused_when_processing_is_not_configured(upload):
    """Valid input: fully validated (including a real decode), then the
    missing provider configuration is reported honestly instead of a
    fabricated job id that nothing could process."""
    response = upload()

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "dependency_unavailable"
    assert isinstance(body["error"]["message"], str) and body["error"]["message"].strip()
    # Actionable: the message names the environment variables to set.
    # R1: vision is the mandatory channel; OCR is optional, so its
    # variables are not demanded when the channel is entirely absent.
    assert "VISION_API_BASE_URL" in body["error"]["message"]
    assert "VISION_MODEL" in body["error"]["message"]
    assert "OCR_API_URL" not in body["error"]["message"]
    assert response.headers["content-type"].startswith("application/json")


def test_frontend_request_shape_is_accepted(auth_client, sample_image):
    """Exactly what frontend/src/services/api.ts sends: multipart body,
    `Accept: application/json`, and no manual Content-Type (the client
    must set the boundary itself)."""
    response = auth_client.post(
        "/api/analysis",
        files={"file": ("scan.png", io.BytesIO(sample_image), "image/png")},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"


def test_missing_file_field_is_422_and_names_the_field(auth_client, sample_image):
    response = auth_client.post(
        "/api/analysis",
        files={"not_the_field": ("a.png", io.BytesIO(sample_image), "image/png")},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert any("file" in part for d in body["error"]["details"] for part in d["loc"])


def test_request_without_any_multipart_body_is_422(auth_client):
    response = auth_client.post("/api/analysis")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_unsupported_media_type_is_415(upload, sample_image):
    response = upload(content=b"%PDF-1.4 fake", filename="doc.pdf", content_type="application/pdf")

    assert response.status_code == 415
    body = response.json()
    assert body["error"]["code"] == "unsupported_media_type"
    assert "not a supported image" in body["error"]["message"]


def test_generic_media_type_with_wrong_extension_is_415(upload):
    response = upload(content=b"xx", filename="doc.pdf", content_type="application/octet-stream")

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


def test_generic_media_type_with_accepted_extension_is_accepted(upload, sample_image):
    response = upload(filename="photo.png", content_type="application/octet-stream")

    # Passes the shared guard + real decode, then the provider gate (503).
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"


def test_empty_file_is_422(upload):
    response = upload(content=b"", filename="empty.png")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_oversized_upload_is_413(upload, monkeypatch):
    monkeypatch.setattr(analysis_routes, "MAX_UPLOAD_BYTES", 1024)

    response = upload(content=b"a" * 2048, filename="big.png")

    assert response.status_code == 413
    body = response.json()
    assert body["error"]["code"] == "payload_too_large"
    assert "1 KB" in body["error"]["message"]


def test_undecodable_image_content_is_422(upload):
    """Right magic bytes, garbage body: D1's decode gate rejects it with
    the canonical envelope — before any provider or database work."""
    response = upload(content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, filename="broken.png")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "Traceback" not in response.text


def test_upload_processing_never_logs_image_bytes(upload, capsys):
    """B1 security requirement: uploaded image contents (including image
    metadata) must never reach the log stream. Pins the current behaviour
    — if a future log statement echoes decoded content, this fails."""
    from PIL import Image, PngImagePlugin

    from app.core.config import Settings
    from app.core.logging import configure_logging

    marker = "SECRET-IMAGE-PAYLOAD-XYZ"
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", marker)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG", pnginfo=info)

    configure_logging(Settings.from_env({}))  # bind handler to captured stdout

    response = upload(content=buffer.getvalue(), filename="secret.png")

    assert response.status_code == 503  # provider gate in this environment
    captured = capsys.readouterr()
    assert marker not in captured.out
    assert marker not in captured.err
