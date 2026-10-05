"""
User registration and login — issues JWTs for dashboard/REST access.

Backend concept — why login uses OAuth2PasswordRequestForm (form-encoded
username/password) instead of a JSON body like every other endpoint in
this project: this is the shape FastAPI's built-in OAuth2PasswordBearer
security scheme expects, which is what makes the "Authorize" button in
/docs work out of the box — clicking it, entering credentials, and every
subsequent "Try it out" call in the docs UI automatically includes the
Bearer token. It's a small inconsistency in request format for a real
usability win during development. A real frontend would just POST that
same form data from a login form, or you could add a JSON-based login
endpoint alongside this one if a frontend needs it.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import rate_limit
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import Token, UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("register", "register_rate_limit_attempts"))],
)
async def register_user(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post(
    "/login",
    response_model=Token,
    dependencies=[Depends(rate_limit("login", "login_rate_limit_attempts"))],
)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    """
    form_data.username holds the email (OAuth2's spec calls it "username"
    regardless of what the underlying credential actually is — we just
    treat it as the email here).
    """
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(form_data.password, user.hashed_password):
        # Deliberately the SAME error for "no such user" and "wrong
        # password" — returning a different message for each would let an
        # attacker enumerate which emails are registered just by trying
        # logins and reading the error text.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(subject=user.email)
    return Token(access_token=access_token)
