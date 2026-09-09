import pytest
from pydantic import ValidationError

from app.config import Settings


def test_development_allows_sqlite_fallback():
    settings = Settings(environment="development")
    assert settings.database_url == ""
    assert settings.secret_key == "la-serene-development-secret-change-me"


def test_production_requires_postgresql_database():
    with pytest.raises(ValidationError, match="HMS_DATABASE_URL is required in production"):
        Settings(environment="production", secret_key="x" * 64)


def test_production_rejects_sqlite_database():
    with pytest.raises(ValidationError, match="must use PostgreSQL"):
        Settings(
            environment="production",
            database_url="sqlite:///la_serene_hms.sqlite3",
            secret_key="x" * 64,
        )


def test_production_rejects_default_or_short_secret():
    with pytest.raises(ValidationError, match="strong non-default secret"):
        Settings(
            environment="production",
            database_url="postgresql+psycopg://user:pass@127.0.0.1:5432/la_serene_hms",
            secret_key="short",
        )


def test_production_accepts_valid_configuration():
    settings = Settings(
        environment="production",
        database_url="postgresql+psycopg://user:pass@127.0.0.1:5432/la_serene_hms",
        secret_key="x" * 64,
        token_expire_minutes=480,
    )
    assert settings.environment == "production"
    assert settings.database_url.startswith("postgresql+psycopg://")
