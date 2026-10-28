"""Authentication request/response schemas (camelCase on the wire)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

#: Name lengths mirror the database columns.
MAX_DISPLAY_NAME = 120


class SignupRequest(BaseModel):
    """Payload of POST /api/auth/signup."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    email: str = Field(min_length=3, max_length=320, description="Login address.")
    password: str = Field(
        min_length=1, max_length=128, description="Password (min 8 chars, policy-checked server-side)."
    )
    display_name: Optional[str] = Field(
        default=None,
        alias="displayName",
        max_length=MAX_DISPLAY_NAME,
        description="Optional name shown in the UI.",
    )


class LoginRequest(BaseModel):
    """Payload of POST /api/auth/login."""

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class AuthUser(BaseModel):
    """The authenticated user, without any sensitive field."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str = Field(min_length=1, max_length=128)
    email: str = Field(min_length=3, max_length=320)
    display_name: Optional[str] = Field(default=None, alias="displayName")


class AuthResponse(BaseModel):
    """Body of a successful signup/login/me call."""

    user: AuthUser
