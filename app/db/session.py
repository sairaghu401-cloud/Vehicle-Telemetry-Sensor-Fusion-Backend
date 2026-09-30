"""
SQLAlchemy engine + session setup.

Backend concept: a "connection pool" (the engine) is created ONCE at app
startup and reused for the app's whole lifetime — opening a fresh TCP
connection to Postgres for every request would be slow and would exhaust
Postgres's max_connections under load. A "session" is a short-lived
per-request workspace that borrows a connection from the pool, tracks
objects you load/change, and commits or rolls back as one unit.

We use the ASYNC engine (asyncpg driver) for FastAPI request handlers,
because FastAPI is an async framework: a sync DB call would block the
whole event loop, stalling every other in-flight request. Alembic
migrations, by contrast, run outside the event loop as a one-off CLI
command, so they use the plain sync driver (psycopg2) for simplicity.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.async_database_url,
    echo=(settings.app_env == "development"),
    pool_pre_ping=True,  # checks a connection is alive before handing it out (avoids stale-connection errors)
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base class all SQLAlchemy ORM models inherit from."""
    pass


async def get_db():
    """
    FastAPI dependency: yields a DB session for the duration of one request,
    then always closes it (even if the request raised an exception).

    Backend concept: this is "dependency injection." Instead of every
    endpoint function creating its own session, FastAPI calls get_db(),
    hands the yielded session to the endpoint as an argument, and runs the
    cleanup code after the `yield` once the endpoint returns. It keeps
    session lifecycle logic in one place instead of copy-pasted everywhere.
    """
    async with AsyncSessionLocal() as session:
        yield session
