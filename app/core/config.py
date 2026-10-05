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
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The placeholder shipped in .env.example and used as the fallback below.
# Anyone who has read this repo knows it, so it must never sign real tokens.
DEFAULT_JWT_SECRET = "change_me_dev_secret_do_not_use_in_prod"
MIN_PRODUCTION_JWT_SECRET_LENGTH = 32


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
    jwt_secret_key: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # App
    app_env: str = "development"

    @model_validator(mode="after")
    def _refuse_weak_jwt_secret_in_production(self) -> "Settings":
        """
        Fail at startup, not at the first forged token.

        JWTs are signed with this secret. If it is the well-known
        placeholder from this public repo (or just short), anyone can mint a
        valid token for any user and the whole REST API is open. Development
        and test runs keep the convenient default; APP_ENV=production does
        not get to.
        """
        if self.app_env.lower() == "production":
            if (
                self.jwt_secret_key == DEFAULT_JWT_SECRET
                or len(self.jwt_secret_key) < MIN_PRODUCTION_JWT_SECRET_LENGTH
            ):
                raise ValueError(
                    "Refusing to start with APP_ENV=production and a weak JWT_SECRET_KEY "
                    f"(must not be the default and must be at least {MIN_PRODUCTION_JWT_SECRET_LENGTH} "
                    "characters). Generate one with: "
                    'python -c "import secrets; print(secrets.token_hex(32))"'
                )
        return self

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
