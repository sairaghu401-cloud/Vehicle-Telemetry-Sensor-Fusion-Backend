"""
Shared pytest fixtures for the whole test suite.

Backend concept — WHY this file does its environment-variable juggling
before any `app.*` import, and why that ordering matters:

`app/core/config.py` builds a `Settings` object via `get_settings()`, which
is `@lru_cache`d — called once, cached forever for the life of the process.
Several modules (`app/db/session.py`, `app/db/redis.py`) call
`get_settings()` and build a long-lived engine/client AT IMPORT TIME, not
per-request. That means: whatever POSTGRES_DB/POSTGRES_HOST/REDIS_* env
vars are set at the *moment those modules are first imported* is what the
entire test run is stuck with — setting them later has no effect.

pytest imports `conftest.py` before it imports any test module in the same
directory, so putting the environment overrides at the very top of this
file, before the first `from app...` import anywhere, guarantees every
module gets built against the TEST database/Redis index rather than your
dev ones. This is also why these tests don't need to override FastAPI's
`get_db` dependency with `app.dependency_overrides` — the engine itself
points at the test database, so both REST endpoints (which use
`Depends(get_db)`) and the WebSocket endpoint (which imports
`AsyncSessionLocal` directly, see app/ws/ingest.py) transparently use it.

Running these tests assumes Postgres and Redis are reachable on
localhost:5432 / localhost:6379 — i.e. `docker compose up -d db redis` (or
the full stack) is already running. See README's "Running the tests"
section.
"""
import os

os.environ["POSTGRES_HOST"] = "localhost"
os.environ["POSTGRES_DB"] = "telemetry_test"
os.environ["REDIS_HOST"] = "localhost"
os.environ["REDIS_DB"] = "15"  # a logical DB index unlikely to collide with dev (default 0)

import asyncio
import uuid

import psycopg2
import pytest
import pytest_asyncio
from contextlib import asynccontextmanager
from httpx import ASGITransport, AsyncClient
from httpx_ws.transport import ASGIWebSocketTransport

from app.core.config import get_settings

# get_settings() is lru_cache'd — if anything (a plugin, a prior import)
# already called it before we set the env vars above, this forces it to
# re-read them. Belt-and-braces: with the ordering described above it
# shouldn't be necessary, but it's one line of insurance against a subtle
# import-order bug being very confusing to debug later.
get_settings.cache_clear()
settings = get_settings()


@pytest.fixture(scope="session")
def event_loop():
    """
    Backend concept — why this override is necessary: the app's async
    Postgres engine and async Redis client (app/db/session.py,
    app/db/redis.py) are module-level singletons, created ONCE at import
    time, exactly like in production (one long-lived connection pool for
    the whole process, not one per request). Their connections get bound
    to whichever asyncio event loop is running at that moment.

    pytest-asyncio's DEFAULT behavior creates a fresh event loop for every
    single test function. That's fine for code with no cross-test shared
    state, but here it means test 1 creates the pool under loop A, then
    test 2 gets a brand-new loop B and tries to reuse those same pooled
    connections — which fails with "Task attached to a different loop" or
    "Event loop is closed," because an asyncio socket connection is tied
    to the loop that created it and can't just hop to another one.

    The fix: give the whole test SESSION one event loop, matching the
    "created once, reused everywhere" lifetime the engine/client already
    assume in production. This is a well-known pattern specifically for
    testing apps with a module-level async engine — not something to
    reach for by default in every async test suite.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _admin_connect():
    """A plain psycopg2 connection to Postgres's own always-present
    'postgres' maintenance database — used only to create/drop the TEST
    database itself, which can't be done from a connection that's
    currently "inside" that same database."""
    conn = psycopg2.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        dbname="postgres",
    )
    conn.autocommit = True  # CREATE/DROP DATABASE can't run inside a transaction block
    return conn


@pytest.fixture(scope="session", autouse=True)
def _test_database(event_loop):
    """
    Runs once per test session, before any test: drops and recreates
    `telemetry_test` from scratch, then creates every table via
    `Base.metadata.create_all` (NOT Alembic — tests care about the schema
    matching the models, not about exercising migration scripts; that's a
    deliberate scope cut, not an oversight).

    Session-scoped + autouse means every test file shares one freshly
    built database for the run, rather than each test file rebuilding it.

    Depends on `event_loop` (and uses `event_loop.run_until_complete`, NOT
    `asyncio.run`) so that the engine's very first connections are opened
    on the SAME loop every test will later run on. `asyncio.run()` spins
    up and tears down its own throwaway loop — using it here would bind
    the connection pool's first connections to a loop that's already gone
    by the time the first real test runs, reproducing the exact
    "attached to a different loop" problem the shared `event_loop` fixture
    exists to avoid.
    """
    conn = _admin_connect()
    try:
        with conn.cursor() as cur:
            # WITH (FORCE) (PG 13+) disconnects any lingering sessions from
            # a previous interrupted test run instead of erroring out.
            cur.execute("DROP DATABASE IF EXISTS telemetry_test WITH (FORCE)")
            cur.execute("CREATE DATABASE telemetry_test")
    finally:
        conn.close()

    # Import here, not at module level: this is the first point at which
    # it's actually safe to import app code, since the env vars above are
    # already set and the test database now exists for the engine to
    # connect to.
    from app.db.session import Base, engine
    import app.models  # noqa: F401 — registers every model on Base.metadata (see app/models/__init__.py)

    async def _create_all():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    event_loop.run_until_complete(_create_all())

    yield

    from app.db.redis import redis_client

    async def _dispose_all():
        await engine.dispose()
        # Closes the pooled Redis connections before the session's event
        # loop (below) shuts down. Skipping this is harmless to
        # correctness — it only shows up as a noisy "Event loop is
        # closed" message from a connection object's __del__ during
        # interpreter shutdown — but it's cheap to avoid and a confusing
        # thing to leave in test output for someone still learning to
        # read these.
        await redis_client.aclose()

    event_loop.run_until_complete(_dispose_all())


@pytest_asyncio.fixture(autouse=True)
async def _clean_state():
    """
    Runs around EVERY test: truncates all tables and flushes the test
    Redis index afterwards, so tests never see data left behind by a
    previous test. TRUNCATE ... RESTART IDENTITY also resets auto-
    increment IDs, so a test asserting `reading_id == 1` behaves the same
    whether it's the first test to run or the fiftieth.
    """
    yield

    from app.db.redis import redis_client
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("TRUNCATE TABLE fused_events, sensor_readings, devices, users RESTART IDENTITY CASCADE")
        )
        await db.commit()

    await redis_client.flushdb()


@pytest_asyncio.fixture
async def client():
    """
    An async HTTP client wired directly to the FastAPI app in-process —
    no real server/socket involved (that's what ASGITransport does: speaks
    the ASGI protocol directly to `app`, no TCP socket, no separate
    thread). This is what every REST-only test uses. WebSocket tests use
    `ws_capable_client` below instead — see its docstring for why it's
    deliberately NOT structured the same way as this fixture.
    """
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@asynccontextmanager
async def ws_capable_client():
    """
    A client that can ALSO speak the WebSocket protocol in-process, via
    httpx-ws's ASGIWebSocketTransport — used by test_ws_ingestion.py.

    This is deliberately a plain async context manager, NOT a
    `pytest_asyncio.fixture`, and tests are expected to open it with
    `async with ws_capable_client() as client:` directly in the test body
    rather than requesting it as a fixture argument. Reason: internally,
    ASGIWebSocketTransport opens an anyio TaskGroup in `__aenter__` and
    closes it in `__aexit__`, and anyio requires a TaskGroup/cancel scope
    to be entered and exited by the SAME asyncio Task. pytest-asyncio,
    however, always runs a yielding async fixture's pre-yield setup and
    post-yield teardown as two SEPARATE top-level asyncio Tasks — true
    regardless of the fixture's scope (function/session/etc). Wiring this
    transport into a fixture (as an earlier version of this file did)
    produces `RuntimeError: Attempted to exit cancel scope in a different
    task than it was entered in` on teardown, every time. Opening and
    closing it entirely within one test function's `async with` block
    keeps the whole lifecycle inside the single Task that runs that test,
    which is what anyio actually requires.
    """
    from app.main import app

    transport = ASGIWebSocketTransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def registered_user(client):
    """Registers a fresh dashboard user with a unique email per test (so
    tests can run in any order/repeatedly without colliding on the unique
    email constraint) and returns (email, password)."""
    email = f"user-{uuid.uuid4().hex[:10]}@example.com"
    password = "a-strong-password-123"
    resp = await client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    return email, password


@pytest_asyncio.fixture
async def auth_headers(client, registered_user):
    """Logs the registered_user in and returns a ready-to-use
    {'Authorization': 'Bearer <token>'} dict — the fixture most tests
    that touch /devices/* will actually want."""
    email, password = registered_user
    resp = await client.post("/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def registered_device(client, auth_headers):
    """Registers a device through the (now auth-protected) API and
    returns the full response body, including the one-time raw api_key —
    exactly what a test needing to act AS that device (e.g. connecting to
    /ws/ingest) needs."""
    device_code = f"TEST-{uuid.uuid4().hex[:8].upper()}"
    resp = await client.post(
        "/devices",
        json={"name": "Test Device", "device_code": device_code},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()
