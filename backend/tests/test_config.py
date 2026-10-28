"""B1 tests: configuration loading, validation, required-variable tracking."""

import pytest

from app.core.config import (
    REQUIRED_ENV_VARS,
    ConfigurationError,
    Settings,
    get_settings,
    reset_settings_cache,
)


def test_defaults_from_empty_environment():
    settings = Settings.from_env({})

    assert settings.app_name == "HeritageGraph AI"
    assert settings.environment == "development"
    assert settings.api_prefix == "/api"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.log_level == "INFO"
    assert settings.log_json is False


def test_no_fabricated_credentials_and_required_vars_reported():
    settings = Settings.from_env({})

    # Credentials are never defaulted to a placeholder value...
    assert settings.database_url is None
    # ...and the missing requirement is reported instead.
    assert "DATABASE_URL" in REQUIRED_ENV_VARS
    assert settings.missing_required == ("DATABASE_URL",)
    assert settings.ready is False


def test_blank_database_url_counts_as_missing():
    settings = Settings.from_env({"DATABASE_URL": "   "})

    assert settings.database_url is None
    assert settings.missing_required == ("DATABASE_URL",)


def test_configured_database_url_is_ready():
    url = "postgresql://user:pw@db.internal/heritage"
    settings = Settings.from_env({"DATABASE_URL": url})

    assert settings.database_url == url
    assert settings.missing_required == ()
    assert settings.ready is True


@pytest.mark.parametrize(
    "env",
    [
        {"PORT": "abc"},
        {"PORT": "0"},
        {"PORT": "70000"},
        {"LOG_LEVEL": "chatty"},
        {"ENVIRONMENT": "staging"},
        {"LOG_JSON": "maybe"},
    ],
)
def test_invalid_values_raise_configuration_error(env):
    with pytest.raises(ConfigurationError):
        Settings.from_env(env)


def test_values_are_normalised():
    settings = Settings.from_env(
        {
            "ENVIRONMENT": "Production",
            "API_PREFIX": "api/v1/",
            "HOST": "0.0.0.0",
            "PORT": "9000",
            "LOG_LEVEL": "debug",
            "LOG_JSON": "true",
            "DATABASE_URL": "sqlite:///./data.db",
        }
    )

    assert settings.environment == "production"
    assert settings.api_prefix == "/api/v1"
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000
    assert settings.log_level == "DEBUG"
    assert settings.log_json is True


def test_log_json_defaults_on_in_production():
    assert Settings.from_env({"ENVIRONMENT": "production"}).log_json is True
    assert Settings.from_env({"ENVIRONMENT": "development"}).log_json is False


def test_summary_never_contains_secrets():
    settings = Settings.from_env(
        {"DATABASE_URL": "postgresql://alice:hunter2@db/heritage"}
    )

    summary = settings.summary()
    assert "postgresql://" not in repr(summary)
    assert summary["database_configured"] is True
    assert summary["missing_required"] == []


def test_sensitive_values_are_collected_for_redaction():
    settings = Settings.from_env(
        {
            "DATABASE_URL": "postgres://u:secretvalue@h/db",
            "VISION_API_KEY": "sk-super-secret-value",
            "APP_NAME": "HeritageGraph AI",
        }
    )

    assert "postgres://u:secretvalue@h/db" in settings.sensitive_values
    assert "sk-super-secret-value" in settings.sensitive_values
    assert "HeritageGraph AI" not in settings.sensitive_values


def test_get_settings_reads_the_process_environment(monkeypatch):
    reset_settings_cache()
    monkeypatch.setenv("PORT", "8123")
    try:
        assert get_settings().port == 8123
    finally:
        reset_settings_cache()
