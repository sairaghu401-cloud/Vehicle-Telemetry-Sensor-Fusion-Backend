"""
Integration tests for /auth/* and the get_current_user dependency that
protects every /devices/* endpoint.

These hit a REAL Postgres (the `telemetry_test` database — see
tests/conftest.py) through the actual FastAPI app, not mocks. That's a
deliberate choice: auth bugs (a wrong status code, a leaked field, the
anti-enumeration behavior) live in the gap between the ORM, the password
hashing, and the HTTP layer — a test that mocks any of those away could
pass while the real thing is broken.
"""
import pytest


async def test_register_returns_user_without_password(client):
    resp = await client.post(
        "/auth/register", json={"email": "new-user@example.com", "password": "a-strong-password"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "new-user@example.com"
    assert "id" in body and "created_at" in body
    # The whole point of UserOut as a separate schema from the User ORM
    # model: these fields must never appear in a response, no matter what.
    assert "password" not in body
    assert "hashed_password" not in body


async def test_register_duplicate_email_is_rejected(client):
    payload = {"email": "dupe@example.com", "password": "a-strong-password"}
    first = await client.post("/auth/register", json=payload)
    assert first.status_code == 201

    second = await client.post("/auth/register", json=payload)
    assert second.status_code == 409


async def test_register_rejects_short_password(client):
    resp = await client.post("/auth/register", json={"email": "short@example.com", "password": "1234567"})
    assert resp.status_code == 422


async def test_register_rejects_invalid_email(client):
    resp = await client.post(
        "/auth/register", json={"email": "not-an-email", "password": "a-strong-password"}
    )
    assert resp.status_code == 422


async def test_login_with_correct_credentials_returns_jwt(client, registered_user):
    email, password = registered_user
    resp = await client.post("/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and len(body["access_token"]) > 20


async def test_login_with_wrong_password_is_rejected(client, registered_user):
    email, _ = registered_user
    resp = await client.post("/auth/login", data={"username": email, "password": "definitely-wrong"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Incorrect email or password"


async def test_login_with_unknown_email_is_rejected(client):
    resp = await client.post(
        "/auth/login", data={"username": "nobody@example.com", "password": "whatever123"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Incorrect email or password"


async def test_wrong_password_and_unknown_email_give_identical_errors(client, registered_user):
    """
    Pins down the anti-enumeration design decision from app/api/auth.py:
    an attacker trying to guess which emails are registered should learn
    NOTHING by comparing the two failure responses — same status, same
    body, byte for byte.
    """
    email, _ = registered_user
    wrong_password = await client.post("/auth/login", data={"username": email, "password": "wrong"})
    unknown_email = await client.post(
        "/auth/login", data={"username": "nobody@example.com", "password": "wrong"}
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


async def test_devices_endpoint_without_token_is_rejected(client):
    resp = await client.get("/devices")
    assert resp.status_code == 401


async def test_devices_endpoint_with_valid_token_succeeds(client, auth_headers):
    resp = await client.get("/devices", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []  # no devices registered yet in this test


async def test_devices_endpoint_with_malformed_token_is_rejected(client):
    resp = await client.get("/devices", headers={"Authorization": "Bearer not.a.real.jwt"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Could not validate credentials"


async def test_devices_endpoint_with_token_for_deleted_user_is_rejected(client, auth_headers):
    """
    A JWT stays cryptographically valid until it expires, even if the
    user it names no longer exists — get_current_user is the thing that
    catches that case (it looks the user up by email on every request,
    not just checking the signature). Simulates it by wiping the users
    table out from under a still-valid token.
    """
    from sqlalchemy import text
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await db.execute(text("DELETE FROM users"))
        await db.commit()

    resp = await client.get("/devices", headers=auth_headers)
    assert resp.status_code == 401
