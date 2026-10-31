"""Smoke-test the configured OCR/vision providers with a real image.

Runs the *production* adapters (``app.integrations.vision_model`` and
``app.integrations.ocr``) against whatever credentials are in the
environment, so a successful run here means the live analysis pipeline
has a working provider behind it — no demo fixtures, no mocks.

Usage (from ``backend/``)::

    # vision (mandatory for live analysis)
    .venv/Scripts/python -m scripts.vision_smoke ../path/to/monument.jpg

    # also exercise the configured OCR provider (whichever one it is)
    .venv/Scripts/python -m scripts.vision_smoke ../path/to/plaque.png --ocr

    # verify ONLY OCR — OCR.space needs nothing but OCR_SPACE_API_KEY
    .venv/Scripts/python -m scripts.vision_smoke ../path/to/plaque.png --ocr-only

    # or Google Cloud Vision, without a vision provider
    OCR_PROVIDER=google_vision GOOGLE_APPLICATION_CREDENTIALS=/path/key.json \
      .venv/Scripts/python -m scripts.vision_smoke ../path/to/plaque.png --ocr-only

Configuration comes from the shell *and* from ``backend/.env`` (the same file
``run_server`` reads), so this command works without exporting anything by
hand; the shell always wins.

Exit codes: 0 = provider answered and output validated; 2 = missing or
invalid configuration; 3 = provider call failed (timeout/auth/rate
limit/malformed output). Values of keys are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys

from app.integrations import ProviderConfigurationError
from app.integrations.ocr import configuration_problem as ocr_configuration_problem
# Aliased: `_smoke` takes a `run_ocr` flag, which would shadow the function.
from app.integrations.ocr import run_ocr as run_ocr_provider
from app.integrations.ocr import selected_provider as selected_ocr_provider
from app.integrations.vision_model import ENV_BASE_URL, ENV_KEY, ENV_MODEL
from app.integrations.vision_model import extract_from_image

#: Extension → MIME (the adapters only need a sane content type).
MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".avif": "image/avif",
}


def _configured(*names: str) -> bool:
    return all((os.environ.get(name) or "").strip() for name in names)


def _load_local_env() -> list[str]:
    """Apply ``backend/.env`` the way ``run_server`` does.

    The documented pre-flight command must see the same configuration the
    server will: an operator who filled in ``backend/.env`` should not have
    to re-export secrets by hand to prove they work. The shell always wins —
    only variables that are unset/blank are filled in, and key values are
    never echoed.
    """
    from scripts.run_server import resolve_environment

    env, notes = resolve_environment()
    for name, value in env.items():
        if value and not (os.environ.get(name) or "").strip():
            os.environ[name] = value
    return notes


async def _smoke(
    image_path: pathlib.Path, *, run_ocr: bool, run_vision: bool = True
) -> int:
    data = image_path.read_bytes()
    content_type = MIME_BY_SUFFIX.get(image_path.suffix.lower(), "image/png")
    print(f"image: {image_path} ({len(data)} bytes, {content_type})")

    # 1. Vision — the channel that turns the image into entities.
    if run_vision and not _configured(ENV_BASE_URL, ENV_KEY, ENV_MODEL):
        missing = [
            name
            for name in (ENV_BASE_URL, ENV_KEY, ENV_MODEL)
            if not _configured(name)
        ]
        print(f"CONFIGURATION ERROR: missing {', '.join(missing)}")
        print("set them in your shell (see .env.example) and re-run.")
        return 2

    if run_vision:
        try:
            extraction = await extract_from_image(
                data, filename=image_path.name, content_type=content_type
            )
        except ProviderConfigurationError as exc:
            print(f"CONFIGURATION ERROR: {exc}")
            return 2
        except Exception as exc:  # noqa: BLE001 — report, never mask
            print(f"VISION FAILED: {type(exc).__name__}: {exc}")
            return 3

        print("\nvision: OK (provenance=%s)" % extraction.provenance)
        print(f"  interpretation: {extraction.interpretation or '(none)'}")
        print(f"  text: {(extraction.text or '(none)')[:200]}")
        print(f"  entities ({len(extraction.entities)}):")
        for entity in extraction.entities:
            print(f"    - {entity.name!r} [{entity.type}] {entity.confidence}")
        print(f"  relationships ({len(extraction.relationships)}):")
        for rel in extraction.relationships:
            print(f"    - {rel.source} -[{rel.label}]-> {rel.target}")

    # 2. OCR — the second channel, using whatever provider is configured
    # (``google_vision`` or a generic HTTP endpoint). The problem message is
    # provider-aware, so it names GOOGLE_* for Google Vision instead of the
    # generic OCR_API_* variables.
    if run_ocr:
        problem = ocr_configuration_problem()
        if problem:
            print(f"\nOCR: not configured — {problem}")
            # An explicitly requested OCR-only check that could not run is a
            # configuration failure; when OCR was an extra, the vision result
            # above still stands.
            return 2 if not run_vision else 0
        print(f"\nocr: provider={selected_ocr_provider()}")

        try:
            result = await run_ocr_provider(
                data, filename=image_path.name, content_type=content_type
            )
        except Exception as exc:  # noqa: BLE001 — report, never mask
            print(f"\nOCR FAILED: {type(exc).__name__}: {exc}")
            return 3
        print(f"\nocr: OK ({len(result.text)} chars)")
        print(f"  text: {result.text[:300] or '(empty — no text in image)'}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the configured vision/OCR providers with a real image."
    )
    parser.add_argument("image", help="path to a JPEG/PNG/WebP/... image")
    parser.add_argument(
        "--ocr", action="store_true", help="also exercise the OCR provider"
    )
    parser.add_argument(
        "--ocr-only",
        action="store_true",
        help="verify only the OCR provider (no vision credentials needed)",
    )
    args = parser.parse_args()

    # ``backend/.env`` is what the server reads; the pre-flight check must
    # agree with it, otherwise "not configured" here and "configured" at
    # runtime would both be true.
    for note in _load_local_env():
        print(f"  \u00b7 {note}")

    path = pathlib.Path(args.image)
    if not path.is_file():
        print(f"file not found: {path}")
        return 2

    try:
        return asyncio.run(
            _smoke(
                path,
                run_ocr=args.ocr or args.ocr_only,
                run_vision=not args.ocr_only,
            )
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())