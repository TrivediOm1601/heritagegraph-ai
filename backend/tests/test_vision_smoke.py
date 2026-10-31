"""The documented credential check must understand the selected OCR provider.

`python -m scripts.vision_smoke <image> --ocr` is the command the README tells
operators to run to prove their credentials work. It used to pre-check the
legacy generic endpoint (``OCR_API_URL``/``OCR_API_KEY``) even when Google
Cloud Vision was selected, so a correctly configured deployment was reported as
"skipped" and never exercised.
"""

import importlib
import sys
from types import SimpleNamespace

import pytest

vision_smoke = importlib.import_module("scripts.vision_smoke")

#: Captured before any fixture replaces it (the isolation fixture below).
REAL_LOAD_LOCAL_ENV = vision_smoke._load_local_env

CLEARED = (
    "OCR_PROVIDER",
    "OCR_API_URL",
    "OCR_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_VISION_API_KEY",
    "GOOGLE_VISION_ACCESS_TOKEN",
)


@pytest.fixture(autouse=True)
def _isolate_from_backend_env_file(monkeypatch):
    """``backend/.env`` is host state, like ``os.environ``.

    The smoke command now loads it (so the documented one-liner works),
    which would otherwise make these tests depend on whatever credentials
    happen to be configured on this machine.
    """
    monkeypatch.setattr(vision_smoke, "_load_local_env", lambda: [])


@pytest.fixture
def image(tmp_path):
    """A file with an image suffix; OCR-only mode never decodes it."""
    path = tmp_path / "plaque.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    return path


def _run(monkeypatch, capsys, image, *args):
    monkeypatch.setattr(sys, "argv", ["vision_smoke.py", str(image), *args])
    code = vision_smoke.main()
    return code, capsys.readouterr().out


def test_ocr_only_points_at_the_provider_switch(monkeypatch, capsys, image):
    for name in CLEARED:
        monkeypatch.delenv(name, raising=False)

    code, out = _run(monkeypatch, capsys, image, "--ocr-only")

    assert code == 2  # the requested check could not run: a configuration error
    assert "OCR_PROVIDER=google_vision" in out


def test_ocr_only_names_the_google_credentials_when_google_is_selected(
    monkeypatch, capsys, image
):
    monkeypatch.setenv("OCR_PROVIDER", "google_vision")
    for name in CLEARED:
        if name != "OCR_PROVIDER":
            monkeypatch.delenv(name, raising=False)

    code, out = _run(monkeypatch, capsys, image, "--ocr-only")

    assert code == 2
    assert "GOOGLE_APPLICATION_CREDENTIALS" in out
    assert "OCR_API_URL" not in out  # the generic variables are not the fix here
    assert "vision: OK" not in out  # vision was not attempted


def test_ocr_only_runs_ocr_without_any_vision_credentials(
    monkeypatch, capsys, image
):
    """The remaining live check must be possible with only GCP credentials."""
    monkeypatch.setenv("OCR_PROVIDER", "google_vision")
    monkeypatch.setenv("GOOGLE_VISION_API_KEY", "not-a-real-key-never-sent")
    seen: dict[str, object] = {}

    async def fake_run_ocr(data, *, filename, content_type):
        seen.update(bytes=len(data), filename=filename, content_type=content_type)
        return SimpleNamespace(text="INSCRIPTION TEXT", languages=("la",))

    monkeypatch.setattr(vision_smoke, "run_ocr_provider", fake_run_ocr)

    code, out = _run(monkeypatch, capsys, image, "--ocr-only")

    assert code == 0, out
    assert seen["bytes"] == len(image.read_bytes())  # the real bytes are sent
    assert seen["content_type"] == "image/png"
    assert "INSCRIPTION TEXT" in out
    assert "ocr: provider=google_vision" in out


def test_local_env_file_is_loaded_like_run_server_does(monkeypatch, tmp_path):
    """Shell wins; ``backend/.env`` only fills what the shell left out."""
    from scripts import run_server

    monkeypatch.setenv("OCR_PROVIDER", "http")  # already set: must survive
    monkeypatch.delenv("OCR_SPACE_API_KEY", raising=False)
    monkeypatch.setattr(vision_smoke, "_load_local_env", REAL_LOAD_LOCAL_ENV)
    monkeypatch.setattr(
        run_server,
        "resolve_environment",
        lambda: ({"OCR_PROVIDER": "ocr_space", "OCR_SPACE_API_KEY": "from-file"}, ["loaded 2 value(s) from .env"]),
    )

    notes = vision_smoke._load_local_env()

    import os

    assert os.environ["OCR_PROVIDER"] == "http"  # the shell was not overwritten
    assert os.environ["OCR_SPACE_API_KEY"] == "from-file"
    assert notes == ["loaded 2 value(s) from .env"]
