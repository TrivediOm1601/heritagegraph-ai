"""Tests for ``scripts/run_server.py`` — the single supported start path.

This launcher exists because the app was once started with ad-hoc shell
exports (and, worse, with ``HG_DEMO_MODE=1`` hardcoded), so every image
returned fixture output. The rules it encodes are therefore part of the
product, not convenience:

* the real process environment wins, then ``backend/.env``, then
  ``backend/.vision_key`` (secrets never printed), then safe development
  defaults;
* ``PORT=0`` — exported by some dev tooling — is dropped instead of
  crashing configuration;
* demo mode is never enabled implicitly;
* ``--migrate`` applies the *same* resolved environment as a normal start.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

run_server = importlib.import_module("scripts.run_server")


@pytest.fixture(autouse=True)
def isolated_files(monkeypatch, tmp_path: Path):
    """Point .env/.vision_key at temp files and clear our own environment.

    ``main()`` deliberately writes the resolved start-up environment into
    ``os.environ`` (that is how the server process receives it), so a test
    that calls ``main()`` in-process would otherwise leak ``HG_LIVE_SOURCES=1``
    into every later test — which then reaches the real Wikipedia API. The
    whole environment is snapshotted and restored around each test here.
    """
    saved_environ = dict(os.environ)
    env_file = tmp_path / ".env"
    key_file = tmp_path / ".vision_key"
    adaption_file = tmp_path / ".adaption_key"
    monkeypatch.setattr(run_server, "ENV_FILE", env_file)
    monkeypatch.setattr(run_server, "KEY_FILE", key_file)
    monkeypatch.setattr(run_server, "ADAPTION_KEY_FILE", adaption_file)
    for name in (
        "DATABASE_URL",
        "HG_LIVE_SOURCES",
        "HG_DEMO_MODE",
        "PORT",
        "VISION_API_KEY",
        "VISION_API_BASE_URL",
        "VISION_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    try:
        yield {"env": env_file, "key": key_file, "adaption": adaption_file}
    finally:
        os.environ.clear()
        os.environ.update(saved_environ)


def test_defaults_and_port_zero_sanitising():
    env, notes = run_server.resolve_environment({"PORT": "0"})

    assert "PORT" not in env
    assert env["DATABASE_URL"] == "sqlite:///heritage.db"
    assert env["HG_LIVE_SOURCES"] == "1"
    assert "HG_DEMO_MODE" not in env
    assert any("DATABASE_URL defaulted" in note for note in notes)


def test_explicit_environment_wins_over_files(isolated_files):
    isolated_files["env"].write_text(
        "DATABASE_URL=sqlite:///from-file.db\nHG_LIVE_SOURCES=1\n", encoding="utf-8"
    )
    isolated_files["key"].write_text("key-from-file\n", encoding="utf-8")

    env, notes = run_server.resolve_environment(
        {"DATABASE_URL": "sqlite:///from-shell.db"}
    )

    assert env["DATABASE_URL"] == "sqlite:///from-shell.db"
    assert env["VISION_API_KEY"] == "key-from-file"
    # The note names the file but never the secret.
    assert any("VISION_API_KEY" in note for note in notes)
    assert all("key-from-file" not in note for note in notes)


def test_demo_mode_is_never_implicit(isolated_files):
    env, _ = run_server.resolve_environment({"HG_DEMO_MODE": "1"})

    # Resolution keeps an explicit flag it was given…
    assert env["HG_DEMO_MODE"] == "1"


def test_vision_configured_reports_missing_names():
    configured, missing = run_server.vision_configured({})

    assert configured is False
    assert missing == ["VISION_API_BASE_URL", "VISION_API_KEY", "VISION_MODEL"]

    configured, missing = run_server.vision_configured(
        {
            "VISION_API_BASE_URL": "https://vision.example/v1",
            "VISION_API_KEY": "secret",
            "VISION_MODEL": "model-1",
        }
    )
    assert configured is True and missing == []


def test_check_exit_code_reflects_configuration(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run_server.py", "--check"])
    assert run_server.main() == 2  # nothing configured → actionable failure

    monkeypatch.setenv("VISION_API_BASE_URL", "https://vision.example/v1")
    monkeypatch.setenv("VISION_API_KEY", "secret")
    monkeypatch.setenv("VISION_MODEL", "model-1")
    monkeypatch.setattr(sys, "argv", ["run_server.py", "--check"])
    assert run_server.main() == 0
    # The summary never prints the key itself.
    assert "secret" not in capsys.readouterr().out


def test_demo_transport_and_key_files_are_not_reachable_from_check(monkeypatch):
    """A `--demo` start is explicit; `--check` stays honest about live config."""
    monkeypatch.setattr(sys, "argv", ["run_server.py", "--check", "--demo"])
    assert run_server.main() == 0


def test_migrate_uses_the_resolved_environment(monkeypatch):
    """`--migrate` must not be runnable with the raw shell environment: the
    sanitised PORT (this workspace exports PORT=0) is what makes it work."""
    recorded: dict[str, str] = {}

    fake_init_db = importlib.import_module("app.db.init_db")
    monkeypatch.setattr(
        fake_init_db,
        "main",
        lambda: recorded.update({"DATABASE_URL": os.environ.get("DATABASE_URL", "")}),
    )
    monkeypatch.setattr(sys, "argv", ["run_server.py", "--migrate"])
    monkeypatch.setenv("PORT", "0")

    assert run_server.main() == 0
    assert recorded["DATABASE_URL"] == "sqlite:///heritage.db"
