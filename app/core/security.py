"""
Auth primitives: API key generation/hashing (devices), password
hashing (users), and JWT creation/verification (dashboard sessions).

Backend concept: same hashing principle for both API keys and user
passwords — we generate/receive a secret, hand or let the client keep the
raw value, and store only a one-way bcrypt HASH. If the database ever
leaks, an attacker gets hashes they can't reverse into working credentials.
One shared CryptContext handles both, since it's the same underlying job
(verify a secret against a stored hash) for two different kinds of secret.
"""
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

settings = get_settings()


def generate_api_key() -> str:
    """
    Generates a random API key like 'sk_live_<43 url-safe chars>'.
    token_urlsafe(32) gives 256 bits of entropy — effectively unguessable.
    The 'sk_live_' prefix is a convention (Stripe popularized it) that lets
    you visually recognize a leaked key in logs/code and know what it is.
    """
    return f"sk_live_{secrets.token_urlsafe(32)}"


def hash_api_key(raw_key: str) -> str:
    return _pwd_context.hash(raw_key)


def verify_api_key(raw_key: str, hashed_key: str) -> bool:
    return _pwd_context.verify(raw_key, hashed_key)


def hash_password(raw_password: str) -> str:
    return _pwd_context.hash(raw_password)


def verify_password(raw_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(raw_password, hashed_password)


def create_access_token(subject: str) -> str:
    """
    Builds a signed JWT for a logged-in user.

    Backend concept — what a JWT actually is: it's NOT encrypted, just
    signed. Anyone can base64-decode a JWT and read its contents (try it
    at jwt.io) — the signature only proves the server issued it and it
    hasn't been tampered with. Never put secrets (passwords, raw API
    keys) inside the token payload; here it holds only the user's email
    (as "sub", the standard claim name for "subject") and an expiry time.

    Why a JWT instead of a server-side session: the server can verify the
    token's signature and expiry using only JWT_SECRET_KEY — no database
    lookup needed on every request, no shared session store to keep in
    sync across multiple API server instances. The tradeoff: a JWT can't
    be revoked before it expires (there's no "delete this session" row to
    remove), which is why JWT_EXPIRE_MINUTES is kept short (1 hour here)
    rather than "log in once, stay logged in forever."
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str | None:
    """Returns the subject (user email) if the token is valid and unexpired, else None."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload.get("sub")
    except JWTError:
        return None
