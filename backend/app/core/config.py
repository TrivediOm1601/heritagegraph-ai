"""B1 — environment-driven application configuration.

Design rules taken from the B1 acceptance criteria:

* **No fabricated credentials.** Infrastructure values such as
  ``DATABASE_URL`` are never given default values. When unset they are
  ``None`` and the variable name is recorded in ``missing_required`` so the
  readiness endpoint can report the failure accurately. The application
  still *starts* (liveness must not depend on configuration).
* **Malformed values fail fast.** An invalid port, log level or
  environment name raises :class:`ConfigurationError` at load time instead
  of being silently coerced into something that "works".
* **Everything is read from the environment** through
  :meth:`Settings.from_env`, which accepts an explicit mapping so tests do
  not have to mutate process state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

#: Environment variables that must be present for the service to report
#: itself ready. ``DATABASE_URL`` is consumed by Module C's repository
#: layer (Member 2 owns this config file as integration lead); extend this
#: tuple only together with the member who owns the corresponding module.
REQUIRED_ENV_VARS: tuple[str, ...] = ("DATABASE_URL",)

#: Every environment variable read by ``Settings.from_env``. Tests clear
#: these so the suite never depends on the host machine's environment.
CONFIG_ENV_VARS: tuple[str, ...] = (
    "APP_NAME",
    "ENVIRONMENT",
    "API_PREFIX",
    "HOST",
    "PORT",
    "LOG_LEVEL",
    "LOG_JSON",
    "DATABASE_URL",
)

VALID_ENVIRONMENTS = frozenset({"development", "test", "production"})
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})

#: Variable-name fragments whose values must never appear in logs.
_SENSITIVE_KEY_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTH",
    "DSN",
    "DATABASE_URL",
)


class ConfigurationError(RuntimeError):
    """Raised when configuration is present but invalid."""


def _is_sensitive_name(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _SENSITIVE_KEY_MARKERS)


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the process configuration."""

    app_name: str
    environment: str
    api_prefix: str
    host: str
    port: int
    log_level: str
    log_json: bool
    #: Raw infrastructure credential. ``None`` means "not configured" —
    #: it is never replaced with a fabricated default.
    database_url: str | None
    #: Names from ``REQUIRED_ENV_VARS`` that are unset or blank.
    missing_required: tuple[str, ...]
    #: Values that must never be written to logs (redaction source).
    sensitive_values: tuple[str, ...]

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """Build settings from ``environ`` (defaults to ``os.environ``)."""
        env: Mapping[str, str] = os.environ if environ is None else environ

        def raw(name: str) -> str:
            value = env.get(name)
            return "" if value is None else value.strip()

        app_name = raw("APP_NAME") or "HeritageGraph AI"

        environment = (raw("ENVIRONMENT") or "development").lower()
        if environment not in VALID_ENVIRONMENTS:
            raise ConfigurationError(
                f"ENVIRONMENT must be one of {sorted(VALID_ENVIRONMENTS)} "
                f"(got {environment!r})."
            )

        api_prefix = raw("API_PREFIX") or "/api"
        if not api_prefix.startswith("/"):
            api_prefix = f"/{api_prefix}"
        api_prefix = api_prefix.rstrip("/") or "/"

        host = raw("HOST") or "127.0.0.1"

        port_raw = raw("PORT") or "8000"
        try:
            port = int(port_raw)
        except ValueError:
            raise ConfigurationError(
                f"PORT must be an integer between 1 and 65535 (got {port_raw!r})."
            ) from None
        if not 1 <= port <= 65535:
            raise ConfigurationError(
                f"PORT must be between 1 and 65535 (got {port})."
            )

        log_level = (raw("LOG_LEVEL") or "INFO").upper()
        if log_level not in VALID_LOG_LEVELS:
            raise ConfigurationError(
                f"LOG_LEVEL must be one of {sorted(VALID_LOG_LEVELS)} "
                f"(got {log_level!r})."
            )

        log_json_raw = raw("LOG_JSON").lower()
        if log_json_raw == "":
            log_json = environment == "production"
        elif log_json_raw in _TRUE_VALUES:
            log_json = True
        elif log_json_raw in _FALSE_VALUES:
            log_json = False
        else:
            raise ConfigurationError(
                f"LOG_JSON must be a boolean such as true/false "
                f"(got {log_json_raw!r})."
            )

        database_url = raw("DATABASE_URL") or None
        missing_required = tuple(name for name in REQUIRED_ENV_VARS if not raw(name))

        # Collect secret-looking values for log redaction. Values shorter
        # than 4 characters are skipped so short tokens cannot redact
        # ordinary text everywhere.
        sensitive = {
            value
            for name, value in env.items()
            if _is_sensitive_name(name) and len(value.strip()) >= 4
        }
        sensitive_values = tuple(sorted(sensitive, key=len, reverse=True))

        return cls(
            app_name=app_name,
            environment=environment,
            api_prefix=api_prefix,
            host=host,
            port=port,
            log_level=log_level,
            log_json=log_json,
            database_url=database_url,
            missing_required=missing_required,
            sensitive_values=sensitive_values,
        )

    @property
    def ready(self) -> bool:
        """True when every required environment variable is configured."""
        return not self.missing_required

    def summary(self) -> dict[str, Any]:
        """Log-safe overview of the configuration (never contains secrets)."""
        return {
            "app": self.app_name,
            "environment": self.environment,
            "api_prefix": self.api_prefix,
            "host": self.host,
            "port": self.port,
            "log_level": self.log_level,
            "log_json": self.log_json,
            "database_configured": self.database_url is not None,
            "missing_required": list(self.missing_required),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached process-wide settings (lazily loaded on first use)."""
    return Settings.from_env()


def reset_settings_cache() -> None:
    """Clear the cached settings; used by tests that mutate the environment."""
    get_settings.cache_clear()
