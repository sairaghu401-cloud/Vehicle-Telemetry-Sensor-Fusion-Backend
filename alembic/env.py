from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Import our app's settings and models so Alembic knows (a) which database
# to connect to and (b) what the target schema looks like.
from app.core.config import get_settings
from app.db.session import Base
import app.models  # noqa: F401 — registers all models on Base.metadata

config = context.config

# Override the placeholder sqlalchemy.url from alembic.ini with the real
# one built from our Settings/.env. This means credentials live in exactly
# one place (.env) instead of being duplicated into alembic.ini and
# potentially committed to git by accident.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# This is what makes `alembic revision --autogenerate` work: Alembic
# compares this metadata (our models) against the actual database schema
# and generates the diff as a migration script.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL without a live DB connection (e.g. to hand to a DBA)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection — the normal path."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
