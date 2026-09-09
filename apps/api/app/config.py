from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


Environment = Literal["development", "test", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HMS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = "development"
    database_url: str = ""
    secret_key: str = "la-serene-development-secret-change-me"
    token_expire_minutes: int = Field(default=480, ge=5, le=1440)

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_production_contract(self):
        if self.environment == "production":
            if not self.database_url:
                raise ValueError("HMS_DATABASE_URL is required in production")
            if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("Production HMS_DATABASE_URL must use PostgreSQL")
            if len(self.secret_key) < 32 or self.secret_key == "la-serene-development-secret-change-me":
                raise ValueError("HMS_SECRET_KEY must be a strong non-default secret in production")
        return self


@lru_cache

def get_settings() -> Settings:
    return Settings()


settings = get_settings()
