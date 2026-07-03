# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Lexicon Models - Pydantic models for lexicon operations.

Provides type-safe request and response models for lexicon API interactions.
These models are used by both CLI and Cortex for consistent validation.

Models are organized into two categories:
- Auth models: Device authorization, login, token management
- Package models: Search, info, download, upload operations

Example:
    from chaoscypher_core.services.lexicon.models import (
        LexiconSearchRequest,
        LexiconPackageInfo,
    )

    request = LexiconSearchRequest(query="medical", tags=["healthcare"])
    # Use with LexiconService or LexiconClient
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field, SecretStr, field_serializer


# Module-private default for the four LexiconSettings request models below.
# The canonical value lives in ``LexiconSettings.url`` (settings.py); this
# private constant only seeds the Pydantic ``Field`` defaults in this
# module, since ``Field(default=...)`` requires a literal at class
# definition time. All non-pydantic call sites resolve the URL via
# ``get_settings().lexicon.url`` so live updates to the setting take
# effect without a process restart.
_DEFAULT_LEXICON_URL = os.environ.get("LEXICON_URL", "https://lexicon.chaoscypher.com")


# =============================================================================
# Auth Models
# =============================================================================


class LexiconDeviceCodeRequest(BaseModel):
    """Request to initiate device authorization flow.

    Used to start OAuth 2.0 Device Authorization Grant (RFC 8628).
    The response contains codes for user authentication.

    Attributes:
        lexicon_url: Lexicon API base URL.
        client_id: OAuth client identifier.
        scope: Requested OAuth scopes.
    """

    lexicon_url: str = Field(default=_DEFAULT_LEXICON_URL, description="Lexicon API base URL")
    client_id: str = Field(default="chaoscypher", description="OAuth client identifier")
    scope: str = Field(default="read write", description="Requested OAuth scopes")


class LexiconDeviceCodeResponse(BaseModel):
    """Response from device authorization request.

    Contains the codes needed for user to complete authentication
    and for client to poll for the access token.

    Attributes:
        device_code: Code for polling token endpoint.
        user_code: Code user enters at verification URL.
        verification_uri: URL where user completes auth.
        verification_uri_complete: URL with code embedded (optional).
        expires_in: Seconds until codes expire.
        interval: Minimum polling interval in seconds.
    """

    device_code: str = Field(description="Code for polling token endpoint")
    user_code: str = Field(description="Code user enters at verification URL")
    verification_uri: str = Field(description="URL where user completes auth")
    verification_uri_complete: str | None = Field(
        default=None, description="URL with code embedded"
    )
    expires_in: int = Field(default=900, description="Seconds until codes expire")
    interval: int = Field(default=5, description="Minimum polling interval in seconds")


class LexiconPollRequest(BaseModel):
    """Request to poll for device token.

    Used after device code request to check if user has completed
    authentication in the browser.

    Attributes:
        device_code: Device code from initial request.
        lexicon_url: Lexicon API base URL.
        client_id: OAuth client identifier.
    """

    device_code: str = Field(description="Device code from initial request")
    lexicon_url: str = Field(default=_DEFAULT_LEXICON_URL, description="Lexicon API base URL")
    client_id: str = Field(default="chaoscypher", description="OAuth client identifier")


class LexiconLoginRequest(BaseModel):
    """Username/password login request.

    Used for direct authentication without device flow.

    Attributes:
        username: Lexicon username.
        password: Lexicon password.
        lexicon_url: Lexicon API base URL.
    """

    username: str = Field(description="Lexicon username")
    password: SecretStr = Field(description="Lexicon password")
    lexicon_url: str = Field(default=_DEFAULT_LEXICON_URL, description="Lexicon API base URL")


class LexiconTokenRequest(BaseModel):
    """Direct token authentication request.

    Used for CI/automation scenarios where token is provided directly.

    Attributes:
        token: JWT access token.
        username: Optional username (for display purposes).
        lexicon_url: Lexicon API base URL.
    """

    token: SecretStr = Field(description="JWT access token")
    username: str | None = Field(default=None, description="Optional username")
    lexicon_url: str = Field(default=_DEFAULT_LEXICON_URL, description="Lexicon API base URL")


class LexiconAuthConfig(BaseModel):
    """Authentication configuration.

    Stores credentials for authenticated lexicon operations.
    This is the Pydantic equivalent of the dataclass AuthConfig.

    Attributes:
        token: JWT access token.
        refresh_token: Token for refreshing access.
        expires_at: Token expiration timestamp (ISO format).
        username: Authenticated username.
    """

    # token + refresh_token use SecretStr so they redact in repr() and
    # logs. The field_serializer below preserves plaintext on
    # model_dump so the disk-backed JSON credential file still
    # round-trips.
    token: SecretStr | None = Field(default=None, description="JWT access token")
    refresh_token: SecretStr | None = Field(default=None, description="Token for refreshing access")
    expires_at: str | None = Field(default=None, description="Token expiration timestamp")
    username: str | None = Field(default=None, description="Authenticated username")

    @field_serializer("token", "refresh_token", when_used="always")
    def _serialize_secret(self, v: SecretStr | None) -> str | None:
        """Unwrap SecretStr to its raw value (or None) for credential file round-trip."""
        return v.get_secret_value() if v is not None else None

    @property
    def is_authenticated(self) -> bool:
        """Check if user has valid auth token."""
        return self.token is not None


class LexiconAuthResponse(BaseModel):
    """Response from authentication operations.

    Returned after successful login, token set, or logout.

    Attributes:
        success: Whether the operation succeeded.
        username: Authenticated username (if applicable).
        lexicon_url: Lexicon URL used for the operation.
        message: Human-readable status message.
    """

    success: bool = Field(description="Whether the operation succeeded")
    username: str | None = Field(default=None, description="Authenticated username")
    lexicon_url: str = Field(description="Lexicon URL used for the operation")
    message: str = Field(description="Human-readable status message")


class LexiconAuthStatus(BaseModel):
    """Current lexicon authentication status.

    Used to check if user is currently authenticated.

    Attributes:
        authenticated: Whether user is authenticated.
        username: Authenticated username (if applicable).
        lexicon_url: Configured lexicon URL.
        token_present: Whether a token is stored.
    """

    authenticated: bool = Field(description="Whether user is authenticated")
    username: str | None = Field(default=None, description="Authenticated username")
    lexicon_url: str | None = Field(default=None, description="Configured lexicon URL")
    token_present: bool = Field(description="Whether a token is stored")


# =============================================================================
# Package Models
# =============================================================================


class LexiconSearchRequest(BaseModel):
    """Package search request.

    Used to search for packages on the lexicon with optional filters.

    Attributes:
        query: Search query string (empty returns all).
        page: Page number (1-indexed).
        limit: Results per page (max 100).
        sort_by: Sort order (relevance, stars, downloads, newest, updated, name).
        is_public: Filter by visibility.
        owner_id: Filter by owner ID.
        conformance_class: Filter by CCX conformance class.
    """

    query: str = Field(default="", description="Search query string (empty returns all)")
    page: int = Field(default=1, ge=1, description="Page number (1-indexed)")
    limit: int = Field(default=20, ge=1, le=100, description="Results per page")
    sort_by: str = Field(
        default="relevance",
        description="Sort by: relevance, stars, downloads, newest, updated, name",
    )
    is_public: bool | None = Field(default=None, description="Filter by visibility")
    owner_id: str | None = Field(default=None, description="Filter by owner ID")
    conformance_class: str | None = Field(
        default=None, description="Filter by CCX conformance class"
    )


class LexiconPackageInfo(BaseModel):
    """Package metadata from Lexicon API.

    Contains full metadata for a package from the lexicon registry.

    Attributes:
        id: Unique repository ID.
        name: Repository/package name.
        description: Package description.
        owner_username: Owner's username.
        owner_name: Owner's display name.
        owner_id: Owner's user ID.
        is_public: Public visibility.
        star_count: Number of stars.
        version_count: Number of published versions.
        download_count: Total downloads across all versions.
        created_at: Unix timestamp (ms).
        updated_at: Unix timestamp (ms).
        conformance_classes: CCX conformance classes the package satisfies.
        is_signed: Whether the package is cryptographically signed.
    """

    id: str = Field(description="Unique repository ID")
    name: str = Field(description="Repository/package name")
    description: str = Field(default="", description="Package description")
    owner_username: str = Field(description="Owner's username")
    owner_name: str = Field(default="", description="Owner's display name")
    owner_id: str = Field(default="", description="Owner's user ID")
    is_public: bool = Field(default=True, description="Public visibility")
    star_count: int = Field(default=0, description="Number of stars")
    version_count: int = Field(default=0, description="Number of published versions")
    download_count: int = Field(default=0, description="Total downloads")
    created_at: int = Field(default=0, description="Unix timestamp (ms)")
    updated_at: int = Field(default=0, description="Unix timestamp (ms)")
    conformance_classes: list[str] | None = Field(
        default=None, description="CCX conformance classes the package satisfies"
    )
    is_signed: bool | None = Field(
        default=None, description="Whether the package is cryptographically signed"
    )

    @property
    def full_name(self) -> str:
        """Get full package name (owner/name)."""
        return f"{self.owner_username}/{self.name}"


class LexiconSearchResponse(BaseModel):
    """Package search results.

    Contains paginated search results from the lexicon.

    Attributes:
        packages: List of matching packages.
        total: Total number of matches.
        page: Current page number.
        limit: Results per page.
    """

    packages: list[LexiconPackageInfo] = Field(description="List of matching packages")
    total: int = Field(description="Total number of matches")
    page: int = Field(description="Current page number")
    limit: int = Field(description="Results per page")


class LexiconDownloadRequest(BaseModel):
    """Request to download a package.

    Attributes:
        owner_username: Package owner's username.
        repo_name: Repository/package name.
        version: Version to download (latest if not specified).
    """

    owner_username: str = Field(description="Package owner's username")
    repo_name: str = Field(description="Repository/package name")
    version: str = Field(default="latest", description="Version to download")


class LexiconUploadRequest(BaseModel):
    """Request metadata for package upload.

    Attributes:
        public: Whether package is publicly visible.
        message: Optional commit/upload message.
    """

    public: bool = Field(default=True, description="Whether package is publicly visible")
    message: str | None = Field(default=None, description="Optional upload message")


class LexiconUploadResponse(BaseModel):
    """Async job envelope returned by the CCX 3.0 hub upload endpoint.

    The hub processes uploads asynchronously: the upload returns a job id
    and a queued status, not the final package metadata.

    Attributes:
        job_id: Identifier for the queued processing job.
        status: Job status reported by the hub (e.g. ``queued``).
        message: Human-readable status message from the hub.
    """

    job_id: str = Field(description="Identifier for the queued processing job")
    status: str = Field(description="Job status reported by the hub")
    message: str = Field(default="", description="Human-readable status message")


__all__ = [
    # Auth models
    "LexiconAuthConfig",
    "LexiconAuthResponse",
    "LexiconAuthStatus",
    "LexiconDeviceCodeRequest",
    "LexiconDeviceCodeResponse",
    # Package models
    "LexiconDownloadRequest",
    "LexiconLoginRequest",
    "LexiconPackageInfo",
    "LexiconPollRequest",
    "LexiconSearchRequest",
    "LexiconSearchResponse",
    "LexiconTokenRequest",
    "LexiconUploadRequest",
    "LexiconUploadResponse",
]
