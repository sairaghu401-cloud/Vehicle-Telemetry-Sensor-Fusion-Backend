"""
Centralized app configuration.

Backend concept: instead of scattering os.getenv() calls across the codebase,
we define one Settings class (pydantic-settings) that reads environment
variables once, validates their types, and gives every other module a single
typed object to import. This means:
  - A typo in an env var name fails loudly at startup, not silently at 3am.
  - Local dev, CI, and prod all just set different env vars / .env files —
    zero code changes between environments.
  - Autocomplete works: settings.postgres_host instead of guessing string keys.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Postgres
    postgres_user: str = "telemetry"
    postgres_password: str = "telemetry_dev_password"
    postgres_db: str = "telemetry"
    postgres_host: str = "db"
    postgres_port: int = 5432

    # Redis
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0

    # Auth
    jwt_secret_key: str = "change_me_dev_secret_do_not_use_in_prod"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # App
    app_env: str = "development"

    @property
    def database_url(self) -> str:
        # psycopg2 (sync) — used by Alembic migrations
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def async_database_url(self) -> str:
        # asyncpg (async) — used by FastAPI request handlers
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        # The trailing /N is Redis's "logical database" index — not a
        # separate server, just a numbered namespace within the same Redis
        # instance. Tests use a different index (see tests/conftest.py) so
        # they can freely FLUSHDB without touching whatever live-state keys
        # your dev server has written while you were testing manually.
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    # lru_cache turns this into a singleton: Settings() is only constructed
    # once per process, and every "get_settings()" call after that returns
    # the same cached instance instead of re-reading env vars each time.
    return Settings()
