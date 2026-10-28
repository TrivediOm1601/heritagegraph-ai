"""B1 — safe application logging.

Two properties matter for the B1 acceptance criteria:

* **Configured from settings** — level and text/JSON formatting come from
  :class:`~app.core.config.Settings`, so ``LOG_LEVEL``/``LOG_JSON`` are
  honoured and reconfiguring is idempotent.
* **Safe** — secrets never reach the log stream. Values of secret-looking
  environment variables (as collected by ``Settings.sensitive_values``) are
  replaced wherever they appear, credentials embedded in URLs are masked as
  a second line of defence, and the formatter covers the full formatted
  record including exception tracebacks.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from typing import Iterable

from .config import Settings

#: Name of the application-wide logger and its child loggers.
LOGGER_NAME = "heritagegraph"

#: Replacement used for anything considered sensitive.
REDACTED = "[redacted]"

#: Masks ``scheme://user:password@host`` credentials even when the whole
#: URL value was never registered as sensitive.
_URL_CREDENTIALS_RE = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")

_TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def redact(text: str, sensitive_values: Iterable[str] = ()) -> str:
    """Return ``text`` with registered secret values and URL credentials masked."""
    for value in sensitive_values:
        if value and value in text:
            text = text.replace(value, REDACTED)
    return _URL_CREDENTIALS_RE.sub(r"\1***:***@", text)


class SafeFormatter(logging.Formatter):
    """Formatter that redacts secrets from the final rendered record."""

    def __init__(self, sensitive_values: Iterable[str] = (), json_mode: bool = False):
        super().__init__(fmt=_TEXT_FORMAT, datefmt=_DATE_FORMAT)
        self._sensitive = tuple(sensitive_values)
        self._json_mode = json_mode

    def format(self, record: logging.LogRecord) -> str:
        if self._json_mode:
            payload: dict[str, object] = {
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%S", time.localtime(record.created)
                ),
                "level": record.levelname,
                "logger": record.name,
                "message": redact(record.getMessage(), self._sensitive),
            }
            if record.exc_info:
                payload["exception"] = redact(
                    self.formatException(record.exc_info), self._sensitive
                )
            return json.dumps(payload, ensure_ascii=False)
        return redact(super().format(record), self._sensitive)


def configure_logging(settings: Settings) -> logging.Logger:
    """(Re)configure the application logger and return it.

    Idempotent: repeated calls replace the previously installed handler
    instead of stacking duplicate output.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(settings.log_level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    for log_filter in list(logger.filters):
        logger.removeFilter(log_filter)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        SafeFormatter(settings.sensitive_values, json_mode=settings.log_json)
    )
    logger.addHandler(handler)
    return logger


def get_logger(suffix: str | None = None) -> logging.Logger:
    """Return the application logger (optionally a namespaced child)."""
    if suffix:
        return logging.getLogger(f"{LOGGER_NAME}.{suffix}")
    return logging.getLogger(LOGGER_NAME)
