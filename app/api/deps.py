"""
Shared FastAPI dependencies used across routers.

Backend concept: this is the same "dependency injection" pattern as
get_db() in app/db/session.py, applied to auth. Rather than every endpoint
that needs "which device is this?" or "which user is this?" reimplementing
lookup/verification logic, we define it once here and endpoints just
declare they need it — FastAPI handles calling it and wiring the result in.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token, verify_api_key
from app.db.session import get_db
from app.models.device import Device
from app.models.user import User

# tokenUrl points Swagger's "Authorize" button at our login endpoint so it
# knows where to POST credentials and how to fetch a token — it doesn't
# affect how THIS dependency validates a token, only how the /docs UI
# behaves.
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


async def authenticate_device_by_api_key(api_key: str, db: AsyncSession) -> Device | None:
    """
    Finds the device whose stored hash matches the given raw API key.

    Backend concept — why this can't be a single indexed lookup: we never
    store the raw key, only its bcrypt hash, and bcrypt hashes are salted
    (two hashes of the same input look completely different). That means
    you can't query "WHERE api_key_hash = hash(candidate)" directly — you
    have to check the candidate against each stored hash with verify().
    At this project's device count that's totally fine; at a much larger
    fleet, a common real-world trick is storing an indexed lookup prefix
    of the key alongside the hash, to narrow the candidate set before
    verifying. Not needed yet, but worth knowing the tradeoff exists.
    """
    result = await db.execute(select(Device).where(Device.is_active.is_(True)))
    candidates = result.scalars().all()
    for device in candidates:
        if verify_api_key(api_key, device.api_key_hash):
            return device
    return None


async def get_current_user(
    token: str = Depends(_oauth2_scheme), db: AsyncSession = Depends(get_db)
) -> User:
    """
    Dependency for every REST endpoint that now requires a logged-in
    dashboard user. FastAPI extracts the 'Authorization: Bearer <token>'
    header automatically (that's what OAuth2PasswordBearer does), hands it
    here, and this decodes + validates it before the endpoint's own code
    ever runs — the same "reject before doing real work" principle used
    for device API keys on the WebSocket endpoint.

    A 401 with WWW-Authenticate: Bearer is the standard way to tell an
    HTTP client "you need to (re-)authenticate," and it's what makes
    browser/tooling auth flows behave correctly (e.g. Swagger's UI knows
    to prompt for login again).
    """
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    email = decode_access_token(token)
    if email is None:
        raise credentials_error

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_error

    return user
