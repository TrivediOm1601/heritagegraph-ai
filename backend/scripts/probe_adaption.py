"""Probe the Adaption inference API before integrating it.

The integration contract must be *verified*, not assumed (the earlier
vision-provider bug came from assuming a response shape). This script is
the verification step, and it prints only what the API actually returns:

* ``--auth`` — one minimal ``POST /api/v1/responses`` to check the key;
* ``--models`` — probe the documented model-listing routes;
* ``--vision`` — send a real base64 image to see whether the endpoint
  accepts image content (the public docs do *not* document it, so this
  must be measured, never assumed).

The key is read from ``backend/.adaption_key`` (git-ignored) or the
``ADAPTION_API_KEY`` environment variable, and is never printed.

Usage::

    python -m scripts.probe_adaption --auth
    python -m scripts.probe_adaption --models
    python -m scripts.probe_adaption --vision
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
KEY_FILE = BACKEND_DIR / ".adaption_key"

ENV_KEY = "ADAPTION_API_KEY"
ENV_BASE_URL = "ADAPTION_API_BASE_URL"
DEFAULT_BASE_URL = "https://api.prod.adaptionlabs.ai/api/v1"

#: Model ids worth probing: the one in the official docs example plus the
#: multimodal-labelled training ids (a *training* label does not imply
#: inference support — that is exactly what this probe measures).
CANDIDATE_MODELS = (
    "qwen3.5-397b",
    "google/gemma-3-27b-it-VLM",
    "google/gemma-3-4b-it-VLM",
)

#: 1x1 PNG — enough to prove whether the endpoint accepts image content.
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB"
    "/AGtE1wAAAAASUVORK5CYII="
)


def load_key() -> str:
    """Key from the environment, else from the git-ignored key file."""
    value = (os.environ.get(ENV_KEY) or "").strip()
    if value:
        return value
    if KEY_FILE.is_file():
        return KEY_FILE.read_text(encoding="utf-8").strip()
    return ""


def base_url() -> str:
    return (os.environ.get(ENV_BASE_URL) or "").strip() or DEFAULT_BASE_URL


def post(path: str, payload: dict, *, key: str, timeout: float = 60.0) -> tuple[int, str]:
    """POST JSON; returns ``(status, body)`` — never raises for HTTP errors."""
    request = urllib.request.Request(
        f"{base_url()}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Adaption documents an optional organization header; the key
            # alone should resolve the default organization.
            "User-Agent": "HeritageGraphAI/1.0 (provider probe)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - probe reports the type only
        return 0, f"{type(exc).__name__}: {exc}"


def get(path: str, *, key: str, timeout: float = 30.0) -> tuple[int, str]:
    request = urllib.request.Request(
        f"{base_url()}{path}",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "HeritageGraphAI/1.0 (provider probe)",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def _preview(body: str, limit: int = 600) -> str:
    """Body excerpt with no credential material (the key is never echoed)."""
    flat = " ".join(body.split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def probe_auth(key: str) -> bool:
    print("== auth + minimal text response ==")
    ok = False
    for model in CANDIDATE_MODELS:
        status, body = post(
            "/responses",
            {"model": model, "input": "Reply with the single word: ready"},
            key=key,
        )
        print(f"  model={model!r} status={status}")
        print(f"    {_preview(body)}")
        if status == 200:
            ok = True
            break
    return ok


def probe_models(key: str) -> None:
    print("== model listing routes ==")
    for path in ("/models", "/autoscientist/models", "/training-models"):
        status, body = get(path, key=key)
        print(f"  GET {path} -> {status}")
        print(f"    {_preview(body)}")


def probe_vision(key: str) -> None:
    print("== vision probe (base64 image content) ==")
    image_data_url = "data:image/png;base64," + base64.b64encode(_PNG_1PX).decode("ascii")
    for model in CANDIDATE_MODELS:
        payload = {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "What colour is this image?"},
                        {"type": "input_image", "image_url": image_data_url},
                    ],
                }
            ],
        }
        status, body = post("/responses", payload, key=key)
        print(f"  model={model!r} status={status}")
        print(f"    {_preview(body)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth", action="store_true")
    parser.add_argument("--models", action="store_true")
    parser.add_argument("--vision", action="store_true")
    parser.add_argument("--base-url", default=None, help="override the API base URL")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    if args.base_url:
        os.environ[ENV_BASE_URL] = args.base_url

    key = load_key()
    print(f"key: {'loaded from ' + (ENV_KEY if os.environ.get(ENV_KEY) else KEY_FILE.name) if key else 'MISSING'}")
    print(f"base url: {base_url()}")
    if not key:
        print(f"No key: set {ENV_KEY} or write {KEY_FILE}.")
        return 2

    ran = False
    auth_ok = True
    if args.models:
        probe_models(key)
        ran = True
    if args.vision:
        probe_vision(key)
        ran = True
    if args.auth or not ran:
        auth_ok = probe_auth(key)
        ran = True
    return 0 if auth_ok else 1


if __name__ == "__main__":
    sys.exit(main())
