"""B1 tests: logging configuration, levels, formats and secret redaction."""

import json

from app.core.config import Settings
from app.core.logging import (
    LOGGER_NAME,
    REDACTED,
    configure_logging,
    get_logger,
    redact,
)

DATABASE_URL = "postgresql://heritage:sup3r-secret-pw@db.internal:5432/heritage"


def test_database_credentials_never_reach_the_log(capsys):
    logger = configure_logging(Settings.from_env({"DATABASE_URL": DATABASE_URL}))

    logger.info("connecting to %s now", DATABASE_URL)
    out = capsys.readouterr().out

    assert "sup3r-secret-pw" not in out
    assert REDACTED in out
    assert "connecting to" in out


def test_url_credentials_masked_even_when_not_registered():
    text = redact("failed on postgres://alice:hunter2@db:5432/x")

    assert "hunter2" not in text
    assert "postgres://***:***@db:5432/x" in text


def test_log_level_is_respected(capsys):
    logger = configure_logging(Settings.from_env({"LOG_LEVEL": "WARNING"}))

    logger.info("quiet message")
    assert "quiet message" not in capsys.readouterr().out

    logger.warning("loud message")
    assert "loud message" in capsys.readouterr().out


def test_json_mode_emits_one_parseable_line(capsys):
    logger = configure_logging(
        Settings.from_env({"LOG_JSON": "true", "DATABASE_URL": DATABASE_URL})
    )

    logger.info("db is %s", DATABASE_URL)
    line = capsys.readouterr().out.strip().splitlines()[-1]

    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == LOGGER_NAME
    assert payload["message"].startswith("db is ")
    assert "sup3r-secret-pw" not in payload["message"]


def test_exception_traceback_is_redacted(capsys):
    logger = configure_logging(Settings.from_env({"DATABASE_URL": DATABASE_URL}))

    try:
        raise RuntimeError(f"boom while opening {DATABASE_URL}")
    except RuntimeError:
        logger.exception("failed")
    out = capsys.readouterr().out

    assert "Traceback" in out
    assert "sup3r-secret-pw" not in out


def test_configure_logging_is_idempotent():
    logger = configure_logging(Settings.from_env({}))
    configure_logging(Settings.from_env({}))

    assert len(logger.handlers) == 1


def test_get_logger_namespaces_under_the_app_logger():
    assert get_logger().name == LOGGER_NAME
    assert get_logger("startup").name == f"{LOGGER_NAME}.startup"
