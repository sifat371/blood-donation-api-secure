import pytest

import app.core.config as config


def _production_settings(**updates):
    base = config.settings.model_copy(
        update={
            "environment": "production",
            "secret_key": "x" * 48,
            "cors_origins": "https://app.example.com",
            "mail_backend": "smtp",
            "smtp_host": "smtp.example.com",
        }
    )
    return base.model_copy(update=updates)


def test_production_rejects_placeholder_secret():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        config.validate_runtime_settings(
            _production_settings(secret_key="change-me-in-production")
        )


def test_production_rejects_empty_secret():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        config.validate_runtime_settings(_production_settings(secret_key=""))


def test_production_rejects_wildcard_cors():
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        config.validate_runtime_settings(_production_settings(cors_origins="*"))


def test_production_requires_smtp_backend():
    with pytest.raises(RuntimeError, match="MAIL_BACKEND"):
        config.validate_runtime_settings(_production_settings(mail_backend="auto"))


def test_production_requires_smtp_host():
    with pytest.raises(RuntimeError, match="SMTP_HOST"):
        config.validate_runtime_settings(_production_settings(smtp_host=""))


def test_secure_production_configuration_is_accepted():
    assert config.validate_runtime_settings(_production_settings()) is None


def test_development_keeps_convenient_defaults():
    dev = config.settings.model_copy(
        update={
            "environment": "development",
            "secret_key": "change-me-in-production",
            "cors_origins": "*",
            "mail_backend": "auto",
            "smtp_host": "",
        }
    )
    assert config.validate_runtime_settings(dev) is None


def test_production_rejects_invalid_business_timezone():
    with pytest.raises(RuntimeError, match="BUSINESS_TIMEZONE"):
        config.validate_runtime_settings(
            _production_settings(business_timezone="Not/A_Real_Zone")
        )
