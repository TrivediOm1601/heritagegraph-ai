"""Smoke-test the configured OCR/vision providers with a real image.

Runs the *production* adapters (``app.integrations.vision_model`` and
``app.integrations.ocr``) against whatever credentials are in the
environment, so a successful run here means the live analysis pipeline
has a working provider behind it — no demo fixtures, no mocks.

Usage (from ``backend/``)::

    # vision (mandatory for live analysis)
    .venv/Scripts/python -m scripts.vision_smoke ../path/to/monument.jpg

    # also exercise OCR when OCR_API_URL / OCR_API_KEY are set
    .venv/Scripts/python -m scripts.vision_smoke ../path/to/plaque.png --ocr

Exit codes: 0 = provider answered and output validated; 2 = missing or
invalid configuration; 3 = provider call failed (timeout/auth/rate
limit/malformed output). Values of keys are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

from app.integrations import ProviderConfigurationError
from app.integrations.ocr import ENV_KEY as OCR_ENV_KEY
from app.integrations.ocr import ENV_URL as OCR_ENV_URL
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
    import os

    return all((os.environ.get(name) or "").strip() for name in names)


async def _smoke(image_path: pathlib.Path, *, run_ocr: bool) -> int:
    data = image_path.read_bytes()
    content_type = MIME_BY_SUFFIX.get(image_path.suffix.lower(), "image/png")
    print(f"image: {image_path} ({len(data)} bytes, {content_type})")

    # 1. Vision — the channel that turns the image into entities.
    if not _configured(ENV_BASE_URL, ENV_KEY, ENV_MODEL):
        missing = [
            name
            for name in (ENV_BASE_URL, ENV_KEY, ENV_MODEL)
            if not _configured(name)
        ]
        print(f"CONFIGURATION ERROR: missing {', '.join(missing)}")
        print("set them in your shell (see .env.example) and re-run.")
        return 2

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

    # 2. OCR — optional second channel (real configured provider only).
    if run_ocr:
        if not _configured(OCR_ENV_URL, OCR_ENV_KEY):
            missing = [
                name
                for name in (OCR_ENV_URL, OCR_ENV_KEY)
                if not _configured(name)
            ]
            print(f"\nOCR: skipped — missing {', '.join(missing)}")
            return 0
        from app.integrations.ocr import run_ocr

        try:
            result = await run_ocr(
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
    args = parser.parse_args()

    path = pathlib.Path(args.image)
    if not path.is_file():
        print(f"file not found: {path}")
        return 2

    try:
        return asyncio.run(_smoke(path, run_ocr=args.ocr))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())