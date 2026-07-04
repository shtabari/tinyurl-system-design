# services/api/app/config.py
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # tolerate unrelated env vars (e.g. PATH) instead of erroring
    )

    # Where's my data (connection string for Postgres).
    database_url: str

    # Who am I on the internet — used to build short_url in responses (transport concern).
    base_url: str

    # Default link TTL. Has a sane default so a bare run doesn't require setting it,
    # but is still env-overridable. 30 days in seconds.
    default_ttl_seconds: int = 60 * 60 * 24 * 30


@lru_cache
def get_settings() -> Settings:
    # Deferred + cached: env is read once, on first *call*, not on import.
    # Swappable in tests via app.dependency_overrides[get_settings].
    return Settings()  # type: ignore[call-arg]