# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Local auth error hierarchy."""

from __future__ import annotations

from chaoscypher_core.exceptions import AuthenticationError


__all__ = [
    "ApiKeyNotFound",
    "CorruptCredentialsFile",
    "CredentialsAlreadyInitialized",
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
    """Credentials file does not exist yet.

    The path is carried as an attribute, never in the message. Every
    ``LocalAuthError`` maps to HTTP 401 and its message is rendered into the
    response envelope, so a message containing the path would hand an
    unauthenticated caller the deployment's credentials location — and a
    pre-setup install answers every request from this arm. Callers that need
    the path for an operator-facing log read ``.path``.
    """

    MESSAGE = "credentials are not initialized"

    def __init__(self, path: str) -> None:
        """Initialize the instance.

        Args:
            path: Location of the missing credentials file. Recorded on the
                exception for server-side logging; never in the message.

        """
        super().__init__(self.MESSAGE)
        self.path = path


class CredentialsAlreadyInitialized(LocalAuthError):  # noqa: N818 — spec-defined name
    """Credentials file already exists (double first-run initialize)."""


class CorruptCredentialsFile(LocalAuthError):  # noqa: N818 — spec-defined name
    """Credentials file exists but cannot be read as valid credential data.

    The path is carried as an attribute, never in the message. Every
    ``LocalAuthError`` maps to HTTP 401 and its message is rendered into the
    response envelope, and this error is reachable from the unauthenticated
    bearer arm of ``/auth/verify`` — so a message containing the path would
    hand an anonymous caller the deployment's credentials location. Callers
    that need the path for an operator-facing log read ``.path``.
    """

    MESSAGE = "credentials file is unreadable or corrupt"

    def __init__(self, path: str) -> None:
        """Initialize the instance.

        Args:
            path: Location of the offending credentials file. Recorded on the
                exception for server-side logging; never in the message.

        """
        super().__init__(self.MESSAGE)
        self.path = path


class InvalidPassword(LocalAuthError):  # noqa: N818 — spec-defined name
    """Password did not match the stored hash."""


class UsernameMismatch(LocalAuthError):  # noqa: N818 — spec-defined name
    """Provided username did not match the stored user."""


class InvalidSessionCookie(LocalAuthError):  # noqa: N818 — spec-defined name
    """Session cookie is malformed, expired, or signature mismatch."""


class ApiKeyNotFound(LocalAuthError):  # noqa: N818 — spec-defined name
    """API key id was not found in credentials file."""
