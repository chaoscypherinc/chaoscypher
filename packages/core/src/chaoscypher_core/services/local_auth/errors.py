# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Local auth error hierarchy."""

from __future__ import annotations

from chaoscypher_core.exceptions import AuthenticationError


__all__ = [
    "ApiKeyNotFound",
    "CorruptCredentialsFile",
    "CredentialsNotInitialized",
    "InvalidPassword",
    "InvalidSessionCookie",
    "LocalAuthError",
    "UsernameMismatch",
]


class LocalAuthError(AuthenticationError):
    """Base exception for local-auth module.

    Extends ``AuthenticationError`` so that every local-auth failure
    maps to HTTP 401 at the API boundary and can be caught alongside
    other authentication errors via the shared ``ChaosCypherException``
    hierarchy.
    """


class CredentialsNotInitialized(LocalAuthError):  # noqa: N818 — spec-defined name
    """Credentials file does not exist yet."""


class CorruptCredentialsFile(LocalAuthError):  # noqa: N818 — spec-defined name
    """Credentials file exists but is not valid JSON."""


class InvalidPassword(LocalAuthError):  # noqa: N818 — spec-defined name
    """Password did not match the stored hash."""


class UsernameMismatch(LocalAuthError):  # noqa: N818 — spec-defined name
    """Provided username did not match the stored user."""


class InvalidSessionCookie(LocalAuthError):  # noqa: N818 — spec-defined name
    """Session cookie is malformed, expired, or signature mismatch."""


class ApiKeyNotFound(LocalAuthError):  # noqa: N818 — spec-defined name
    """API key id was not found in credentials file."""
