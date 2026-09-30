"""Pydantic schemas for User accounts and JWT tokens."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, description="At least 8 characters")


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    """Returned by POST /auth/login. The client sends this back as
    'Authorization: Bearer <access_token>' on every subsequent request."""

    access_token: str
    token_type: str = "bearer"
