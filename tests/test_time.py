from datetime import datetime

import pytest

from app.core.config import settings
from app.core.time import business_today, business_timezone, utc_now


def test_utc_now_is_utc_naive_for_legacy_columns():
    value = utc_now()
    assert isinstance(value, datetime)
    assert value.tzinfo is None


def test_business_timezone_defaults_to_dhaka():
    assert settings.business_timezone == "Asia/Dhaka"
    assert business_timezone().key == "Asia/Dhaka"
    assert business_today() is not None


def test_invalid_business_timezone_fails_clearly(monkeypatch):
    monkeypatch.setattr(settings, "business_timezone", "Not/A_Real_Zone")
    with pytest.raises(RuntimeError, match="BUSINESS_TIMEZONE"):
        business_timezone()
