# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""DTOs for local auth."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr

from chaoscypher_core import policy


class AuthStatusResponse(BaseModel):
    """Current auth state: whether setup is needed, whether the caller is authenticated."""

    setup_needed: bool
    authenticated: bool
    username: str | None = None


class SetupRequest(BaseModel):
    """First-run admin setup payload."""

    username: str = Field(
        min_length=policy.USERNAME_MIN_LENGTH,
        max_length=policy.USERNAME_MAX_LENGTH,
    )
    password: SecretStr = Field(
        min_length=policy.PASSWORD_MIN_LENGTH,
        max_length=policy.PASSWORD_MAX_LENGTH,
    )


class LoginRequest(BaseModel):
    """Password login payload."""

    username: str
    password: SecretStr


class ChangePasswordRequest(BaseModel):
    """Rotate the admin password."""

    old_password: SecretStr
    new_password: SecretStr = Field(
        min_length=policy.PASSWORD_MIN_LENGTH,
        max_length=policy.PASSWORD_MAX_LENGTH,
    )


class ChangeUsernameRequest(BaseModel):
    """Rename the admin account (password required)."""

    password: SecretStr
    new_username: str = Field(
        min_length=policy.USERNAME_MIN_LENGTH,
        max_length=policy.USERNAME_MAX_LENGTH,
    )


class UserResponse(BaseModel):
    """Minimal "who am I" response."""

    username: str


class ApiKeyCreateRequest(BaseModel):
    """Mint a new API key with a human-readable label."""

    name: str = Field(min_length=1, max_length=policy.API_KEY_NAME_MAX_LENGTH)


class ApiKeyCreateResponse(BaseModel):
    """Response for a freshly minted API key — includes plaintext key shown ONCE."""

    id: str
    name: str
    key: str
    created_at: str


class ApiKeyListItem(BaseModel):
    """Safe API key listing (no secret material)."""

    id: str
    name: str
    created_at: str
    last_used_at: str | None
