"""
Tests for Settings validation (app/core/config.py).

Settings is built directly here with `_env_file=None`, so the developer's
real .env can't leak into the result, and `get_settings()` (the cached
singleton the rest of the app uses) is never touched.
"""
import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_JWT_SECRET, Settings

STRONG_SECRET = "a" * 64


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_development_allows_the_default_secret():
    s = make_settings(app_env="development")
    assert s.jwt_secret_key == DEFAULT_JWT_SECRET


def test_production_rejects_the_default_secret():
    with pytest.raises(ValidationError, match="weak JWT_SECRET_KEY"):
        make_settings(app_env="production")


def test_production_rejects_a_short_secret():
    with pytest.raises(ValidationError, match="weak JWT_SECRET_KEY"):
        make_settings(app_env="production", jwt_secret_key="too-short")


def test_production_accepts_a_strong_secret():
    s = make_settings(app_env="production", jwt_secret_key=STRONG_SECRET)
    assert s.jwt_secret_key == STRONG_SECRET


def test_production_check_is_case_insensitive():
    with pytest.raises(ValidationError):
        make_settings(app_env="Production")
