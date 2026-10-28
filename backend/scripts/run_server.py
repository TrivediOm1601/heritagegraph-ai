"""Launch the backend with the credentials the app actually needs.

Why this exists (root cause of "every image returns Ada Lovelace"):

* The live vision credentials lived only in ad-hoc shell exports — no
  script, config or documented command read ``.vision_key``, and
* the documented run command hardcoded ``HG_DEMO_MODE=1``.

So any normal start of the backend ran the fixture pipeline, whatever
code changes had been made behind it. This launcher is the single
supported way to start the app: it resolves configuration the way the
pipeline expects and **never enables demo mode implicitly**.

Configuration resolution (first value wins)::

    1. real process environment       (explicit shell config wins)
    2. ``backend/.env``               (non-secret values: base URL, model,
                                       DATABASE_URL, HG_LIVE_SOURCES …)
    3. ``backend/.vision_key``        (single-line secret → VISION_API_KEY,
                                       only when the variable is unset)
    4. built-in development defaults  (DATABASE_URL, HG_LIVE_SOURCES=1)

Usage (identical on every platform — it is just Python)::

    python -m scripts.run_server                 # live analysis
    python -m scripts.run_server --demo          # explicit fixture demo
    python -m scripts.run_server --check         # print resolved config
    python -m scripts.run_server --migrate       # apply DB migrations, exit
    python -m scripts.run_server --port 8125

``--migrate`` runs Module C's migration entry point through this same
resolution path, so a fresh checkout migrates with exactly the environment
the server will use (``npm run setup`` calls it) — including the sanitising
described above, which ``app.db.init_db`` does not do on its own.

``PORT=0`` (exported by some dev tooling, rejected by the backend config)
is dropped automatically. Secrets are never printed, logged or written.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"
KEY_FILE = BACKEND_DIR / ".vision_key"
#: Optional synthesis provider key (Adaption). Loaded into ADAPTION_API_KEY
#: only when the variable is unset and synthesis is opted in.
ADAPTION_KEY_FILE = BACKEND_DIR / ".adaption_key"

#: Set only by an explicit ``--demo`` flag or the process environment.
DEMO_VAR = "HG_DEMO_MODE"


def _parse_env_file(path: pathlib.Path) -> dict[str, str]:
    """Minimal ``KEY=VALUE`` reader (comments and blank lines ignored)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def resolve_environment(
    environ: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Merge environment sources; return ``(env, notes)`` — no secrets in notes."""
    env = dict(os.environ if environ is None else environ)
    notes: list[str] = []

    # 1. .env supplies non-secret configuration the shell does not have.
    file_values = _parse_env_file(ENV_FILE)
    for key, value in file_values.items():
        if not (env.get(key) or "").strip():
            env[key] = value
    if file_values:
        notes.append(f"loaded {len(file_values)} value(s) from {ENV_FILE.name}")

    # 2. The key file supplies VISION_API_KEY only when it is unset.
    if not (env.get("VISION_API_KEY") or "").strip() and KEY_FILE.is_file():
        secret = KEY_FILE.read_text(encoding="utf-8").strip()
        if secret:
            env["VISION_API_KEY"] = secret
            notes.append(f"loaded VISION_API_KEY from {KEY_FILE.name} (value hidden)")

    # 2b. Adaption (optional synthesis) key, same rule and same secrecy.
    if (
        not (env.get("ADAPTION_API_KEY") or "").strip()
        and ADAPTION_KEY_FILE.is_file()
    ):
        secret = ADAPTION_KEY_FILE.read_text(encoding="utf-8").strip()
        if secret:
            env["ADAPTION_API_KEY"] = secret
            notes.append(
                f"loaded ADAPTION_API_KEY from {ADAPTION_KEY_FILE.name} (value hidden)"
            )

    # 3. Development defaults — never demo mode.
    if not (env.get("DATABASE_URL") or "").strip():
        env["DATABASE_URL"] = "sqlite:///heritage.db"
        notes.append("DATABASE_URL defaulted to sqlite:///heritage.db")
    if not (env.get("HG_LIVE_SOURCES") or "").strip():
        env["HG_LIVE_SOURCES"] = "1"
        notes.append("HG_LIVE_SOURCES=1 (live Wikipedia records)")

    # 4. Some dev shells export PORT=0, which the config rightly rejects.
    if (env.get("PORT") or "").strip() in {"", "0"}:
        env.pop("PORT", None)

    return env, notes


def vision_configured(env: dict[str, str]) -> tuple[bool, list[str]]:
    """``(configured, missing_names)`` for the mandatory vision channel."""
    missing = [
        name
        for name in ("VISION_API_BASE_URL", "VISION_API_KEY", "VISION_MODEL")
        if not (env.get(name) or "").strip()
    ]
    return not missing, missing


def main() -> int:
    # Windows consoles default to cp1252, which cannot encode the box
    # characters this summary uses — force UTF-8 output.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover - exotic streams
        pass

    parser = argparse.ArgumentParser(
        description="Start the HeritageGraph AI backend with real configuration."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="explicitly run the labelled fixture pipeline (HG_DEMO_MODE=1)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="print the resolved configuration (names/status only) and exit",
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="apply pending database migrations with the resolved environment, then exit",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="with --migrate: also load the curated development records (idempotent)",
    )
    args = parser.parse_args()

    env, notes = resolve_environment()
    if args.demo:
        env[DEMO_VAR] = "1"
        notes.append("demo mode enabled by --demo (results carry a DEMO MODE warning)")
    else:
        # Never inherit an ambient demo flag unless it was deliberate.
        env.pop(DEMO_VAR, None)

    configured, missing = vision_configured(env)

    print("HeritageGraph AI — startup configuration")
    for note in notes:
        print(f"  · {note}")
    print(f"  · mode: {'DEMO (fixture)' if args.demo else 'LIVE (real providers)'}")
    if configured:
        print(
            f"  · vision: configured -> {env['VISION_API_BASE_URL']} "
            f"(model {env['VISION_MODEL']})"
        )
    else:
        print(f"  · vision: NOT configured — missing {', '.join(missing)}")
        print(
            "    POST /api/analysis will answer 503 until these are set "
            "(in .env / the shell), or restart with --demo for labelled fixtures."
        )
    # R7: report the *selected* OCR provider, not just the generic adapter's
    # variables — Google Cloud Vision needs no OCR_API_URL/KEY at all.
    from app.integrations.ocr import configuration_problem as ocr_problem

    ocr_issue = ocr_problem(env)
    provider_label = (env.get("OCR_PROVIDER") or "http").strip().lower()
    if ocr_issue is None:
        print(f"  · ocr: configured (provider {provider_label})")
    else:
        print(
            "  · ocr: not usable (vision-only; each run carries an OCR-skipped "
            "warning) — " + ocr_issue
        )
    synthesis_on = (env.get("HG_SYNTHESIS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    adaption_ready = bool(
        (env.get("ADAPTION_API_KEY") or "").strip()
        and (env.get("ADAPTION_MODEL") or "").strip()
    )
    if not synthesis_on:
        print("  · synthesis: off (HG_SYNTHESIS unset; explanations stay extractive)")
    elif adaption_ready:
        print(
            "  · synthesis: Adaption configured -> model "
            f"{env['ADAPTION_MODEL']} (hosted-model availability is checked per call)"
        )
    else:
        print(
            "  · synthesis: requested but NOT configured — missing "
            + ", ".join(
                name
                for name in ("ADAPTION_API_KEY", "ADAPTION_MODEL")
                if not (env.get(name) or "").strip()
            )
        )

    if args.check:
        return 0 if (configured or args.demo) else 2

    # Apply the resolved environment *before* importing the app: settings
    # are read at import time (and by the migration entry point below).
    os.environ.clear()
    os.environ.update(env)

    if args.migrate:
        # Module C's migration entry point, through the environment resolved
        # above (so PORT=0 from some dev tooling cannot break it, and
        # DATABASE_URL comes from the same place the server will read it).
        from app.db import init_db

        init_db.main()  # prints the head revision; honours --seed from argv
        return 0

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        log_level=(os.environ.get("LOG_LEVEL") or "info").lower(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())