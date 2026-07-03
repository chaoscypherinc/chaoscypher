# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Lexicon Client - HTTP client for Lexicon API.

Provides a robust HTTP client with retry logic, authentication,
and structured error handling for lexicon API interactions.

This client is framework-agnostic and can be used by CLI, Cortex, or Neuron.
Authentication tokens are managed externally (storage is caller's responsibility).

Example:
    from chaoscypher_core.services.lexicon import LexiconClient, AuthConfig

    # Create client with auth
    auth = AuthConfig(token="jwt-token")
    client = LexiconClient(auth=auth)

    # Search packages
    results = await client.search("medical")

    # Download package
    archive = await client.download("john/medical", "1.0.0")
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from chaoscypher_core.exceptions import ExternalServiceError


if TYPE_CHECKING:
    from collections.abc import Mapping

logger = structlog.get_logger(__name__)

# API path suffix appended to base URL when constructing the full API URL
_API_PATH = os.environ.get("LEXICON_API_PATH", "/api/v1")


class LexiconClientError(ExternalServiceError):
    """Hub API error with structured details.

    Attributes:
        status_code: HTTP status code.
        message: Error message.
        details: Additional error details.
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize hub client error.

        Args:
            status_code: HTTP status code.
            message: Error message.
            details: Additional error details.
        """
        error_details = details or {}
        error_details["status_code"] = status_code
        super().__init__(service_name="Lexicon", reason=message, details=error_details)
        # Preserve the raw message for callers that use e.message directly.
        self.message = message
        self.status_code = status_code


@dataclass
class AuthConfig:
    """Authentication configuration for lexicon API.

    Supports optional persistence to a JSON file for CLI usage.
    The default auth file path uses platformdirs (XDG-compliant):
    ``~/.config/chaoscypher/auth.json`` on Linux.

    Attributes:
        token: JWT access token.
        refresh_token: Token for refreshing access.
        expires_at: Token expiration timestamp (ISO format).
        username: Authenticated username.
    """

    token: str | None = None
    refresh_token: str | None = None
    expires_at: str | None = None
    username: str | None = None

    @property
    def is_authenticated(self) -> bool:
        """Check if user has valid auth token."""
        return self.token is not None

    @staticmethod
    def _default_auth_file() -> Path:
        """Get the default auth file path (XDG-compliant).

        Returns:
            Path to ``auth.json`` in the user config directory.
        """
        import platformdirs

        config_dir = Path(platformdirs.user_config_dir("chaoscypher", appauthor=False))
        return config_dir / "auth.json"

    @classmethod
    def load(cls, auth_file: Path | None = None) -> AuthConfig:
        """Load auth config from disk.

        Args:
            auth_file: Path to the auth JSON file. Uses XDG default if None.

        Returns:
            AuthConfig instance (empty if no config file exists).
        """
        path = auth_file or cls._default_auth_file()
        if not path.exists():
            return cls()

        try:
            data = json.loads(path.read_text())
            return cls(
                token=data.get("token"),
                refresh_token=data.get("refresh_token"),
                expires_at=data.get("expires_at"),
                username=data.get("username"),
            )
        except json.JSONDecodeError, OSError:
            return cls()

    def save(self, auth_file: Path | None = None) -> None:
        """Save auth config to disk.

        Args:
            auth_file: Path to the auth JSON file. Uses XDG default if None.
        """
        path = auth_file or self._default_auth_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "token": self.token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "username": self.username,
        }
        path.write_text(json.dumps(data, indent=2))

    def clear(self, auth_file: Path | None = None) -> None:
        """Clear auth config from disk and reset fields.

        Args:
            auth_file: Path to the auth JSON file. Uses XDG default if None.
        """
        path = auth_file or self._default_auth_file()
        if path.exists():
            path.unlink()
        self.token = None
        self.refresh_token = None
        self.expires_at = None
        self.username = None


@dataclass
class DeviceCodeResponse:
    """Response from device authorization request.

    This follows RFC 8628 (OAuth 2.0 Device Authorization Grant).

    Attributes:
        device_code: Code for polling token endpoint.
        user_code: Code user enters at verification URL.
        verification_uri: URL where user completes auth.
        verification_uri_complete: URL with code embedded (optional).
        expires_in: Seconds until codes expire.
        interval: Minimum polling interval in seconds.
    """

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    expires_in: int = 900  # 15 minutes default
    interval: int = 5  # 5 seconds default

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceCodeResponse:
        """Create from API response dictionary."""
        return cls(
            device_code=data.get("device_code", ""),
            user_code=data.get("user_code", ""),
            verification_uri=data.get("verification_uri", ""),
            verification_uri_complete=data.get("verification_uri_complete"),
            expires_in=data.get("expires_in", 900),
            interval=data.get("interval", 5),
        )


@dataclass
class PackageInfo:
    """Package metadata from lexicon.

    Under the CCX 3.0 hub contract a package is no longer typed by a fixed
    ``package_type`` taxonomy; conformance is described instead by the set of
    CCX conformance classes the package satisfies (populated by
    ``get_package_info``, not by ``upload``).

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
        download_count: Total downloads.
        created_at: Unix timestamp (ms).
        updated_at: Unix timestamp (ms).
        version: Package version (set when downloading specific version).
        conformance_classes: CCX conformance classes the package satisfies
            (None when the hub did not report them).
        is_signed: Whether the package is cryptographically signed
            (None when the hub did not report it).
    """

    id: str
    name: str
    owner_username: str
    description: str = ""
    owner_name: str = ""
    owner_id: str = ""
    is_public: bool = True
    star_count: int = 0
    version_count: int = 0
    download_count: int = 0
    created_at: int = 0
    updated_at: int = 0
    version: str = ""
    conformance_classes: list[str] | None = None
    is_signed: bool | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackageInfo:
        """Create from API response dictionary."""
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            owner_username=data.get("ownerUsername", ""),
            owner_name=data.get("ownerName", ""),
            owner_id=data.get("ownerId", ""),
            is_public=data.get("isPublic", True),
            star_count=data.get("starCount", 0),
            version_count=data.get("versionCount", 0),
            download_count=data.get("downloadCount", 0),
            created_at=data.get("createdAt", 0),
            updated_at=data.get("updatedAt", 0),
            version=data.get("version", ""),
            conformance_classes=data.get("conformanceClasses"),
            is_signed=data.get("isSigned"),
        )

    @property
    def full_name(self) -> str:
        """Get full package name (owner/name)."""
        return f"{self.owner_username}/{self.name}"


@dataclass
class UploadResult:
    """Async job envelope returned by the CCX 3.0 hub upload endpoint.

    The hub processes uploaded packages asynchronously: the upload POST
    enqueues a job and returns ``202`` with the standard envelope
    ``{"data": {jobId, status, message}}`` (unwrapped by ``upload()`` before
    ``from_dict``), NOT the final ``PackageInfo``. Callers poll the hub job
    endpoint (out of band) for completion.

    Attributes:
        job_id: Identifier for the queued processing job.
        status: Job status reported by the hub (e.g. ``queued``).
        message: Human-readable status message from the hub.
    """

    job_id: str
    status: str
    message: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UploadResult:
        """Create from the hub job-envelope response dictionary."""
        return cls(
            job_id=data.get("jobId", ""),
            status=data.get("status", ""),
            message=data.get("message", ""),
        )


class LexiconClient:
    """HTTP client for Lexicon API.

    Provides authenticated HTTP requests with automatic retry logic,
    exponential backoff, and structured error handling.

    This client is framework-agnostic - it doesn't handle token storage.
    The caller is responsible for persisting and loading AuthConfig.

    Attributes:
        base_url: Lexicon API base URL.
        timeout: Request timeout in seconds.

    Example:
        async with LexiconClient() as client:
            await client.login("username", "password")
            packages = await client.search("medical")
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 30.0,
        upload_timeout: float = 300.0,
        max_retries: int = 4,
        retry_backoff: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0),
        auth: AuthConfig | None = None,
    ) -> None:
        """Initialize lexicon API client.

        Args:
            base_url: Lexicon API base URL. Can be either the server URL
                (e.g., https://lexicon.chaoscypher.com) or the full API URL
                (e.g., https://lexicon.chaoscypher.com/api/v1). The API path will
                be appended automatically if not present. ``None``
                (default) reads ``settings.lexicon.url``.
            timeout: Default request timeout in seconds.
            upload_timeout: Timeout for upload operations in seconds.
            max_retries: Maximum retry attempts for failed requests.
            retry_backoff: Exponential backoff delays in seconds for each retry.
            auth: Optional authentication configuration.
        """
        if base_url is None:
            from chaoscypher_core.app_config import get_settings

            base_url = get_settings().lexicon.url
        url = base_url.rstrip("/")
        # Ensure URL includes API path - append if not present
        if not url.endswith(_API_PATH.rstrip("/")):
            url = f"{url}{_API_PATH}"
        self.base_url = url
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.auth = auth or AuthConfig()
        self._client: httpx.AsyncClient | None = None
        # Separate cached clients for download (follow_redirects=False) and
        # upload (multipart) operations. Each has its own timeout budget.
        self._download_client: httpx.AsyncClient | None = None
        self._upload_client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> LexiconClient:
        """Async context manager entry."""
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *args: object) -> None:
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _get_general_client(self) -> httpx.AsyncClient:
        """Return a cached httpx client for general JSON API requests.

        Used as a fallback when the caller does not use the async context
        manager (i.e. ``self._client`` is ``None``). Timeout is sourced from
        ``self.timeout`` which was set from ``settings.lexicon.timeout`` by
        the constructor.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _get_download_client(self) -> httpx.AsyncClient:
        """Return a cached httpx client for download operations.

        Uses ``upload_timeout`` as the per-request timeout budget and
        disables automatic redirect following so per-hop URL safety
        validation is preserved. No explicit ``max_connections`` limit —
        no settings key exists for it; httpx default (100) is used.
        """
        if self._download_client is None:
            self._download_client = httpx.AsyncClient(
                timeout=self.upload_timeout,
                follow_redirects=False,
            )
        return self._download_client

    def _get_upload_client(self) -> httpx.AsyncClient:
        """Return a cached httpx client for upload (multipart POST) operations.

        Uses ``upload_timeout`` as the per-request timeout budget. No
        explicit ``max_connections`` limit — no settings key exists for it.
        """
        if self._upload_client is None:
            self._upload_client = httpx.AsyncClient(timeout=self.upload_timeout)
        return self._upload_client

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with auth token if available."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "chaoscypher/1.0",
        }
        if self.auth.is_authenticated:
            headers["Authorization"] = f"Bearer {self.auth.token}"
        return headers

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_data: Mapping[str, Any] | None = None,
        retry: bool = True,
    ) -> dict[str, Any]:
        """Make HTTP request with retry logic.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            endpoint: API endpoint (relative to base_url).
            params: Query parameters.
            json_data: JSON request body.
            retry: Enable retry with exponential backoff.

        Returns:
            JSON response as dictionary.

        Raises:
            LexiconClientError: On HTTP error or API error response.
            ExternalServiceError: On network error after retries (covers
                the "Lexicon not deployed" case so the cortex handler
                maps it to HTTP 503 instead of 500 with a stack trace).
            ExternalServiceError: Defensive fallback when retries exhaust with no
                captured error object (maps to HTTP 503).
        """
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers()

        async def make_request() -> httpx.Response:
            """Issue the HTTP request using the pooled client when present."""
            active_client = self._client if self._client is not None else self._get_general_client()
            return await active_client.request(
                method,
                url,
                params=params,
                json=json_data,
                headers=headers,
            )

        last_error: Exception | None = None
        attempts = self.max_retries if retry else 1

        for attempt in range(attempts):
            try:
                response = await make_request()

                # Handle HTTP errors
                if response.status_code >= 400:
                    try:
                        error_data = response.json()
                        fallback_msg = response.reason_phrase or "Unknown error"
                        message = error_data.get("message", fallback_msg)
                        details = error_data.get("details", {})
                    except Exception:
                        message = response.reason_phrase or "Unknown error"
                        details = {}

                    raise LexiconClientError(
                        status_code=response.status_code,
                        message=message,
                        details=details,
                    )

                # Return successful response
                if response.status_code == 204:
                    return {}
                # Handle empty response body
                if not response.content:
                    logger.error(
                        "lexicon_empty_response",
                        url=url,
                        status_code=response.status_code,
                        headers=dict(response.headers),
                    )
                    raise LexiconClientError(
                        status_code=response.status_code,
                        message="Empty response from server",
                        details={"url": url},
                    )
                try:
                    data: dict[str, Any] = response.json()
                    return data
                except ValueError as json_err:
                    logger.exception(
                        "lexicon_invalid_json",
                        url=url,
                        status_code=response.status_code,
                        content_length=len(response.content),
                        content_type=response.headers.get("content-type"),
                        body_preview=response.text[:500] if response.text else "(empty text)",
                    )
                    raise LexiconClientError(
                        status_code=response.status_code,
                        message=f"Invalid JSON response: {json_err}",
                        details={
                            "url": url,
                            "body_preview": response.text[:200] if response.text else "(empty)",
                            "content_type": response.headers.get("content-type"),
                        },
                    ) from json_err

            except httpx.RequestError as e:
                last_error = e
                # ConnectError means "the service isn't there" — retrying
                # with backoff just lengthens the UI hang. Break out
                # immediately and let the post-loop block convert this
                # to ExternalServiceError → HTTP 503.
                if isinstance(e, httpx.ConnectError):
                    logger.warning(
                        "lexicon_unreachable",
                        url=url,
                        error=str(e),
                    )
                    break
                if attempt < attempts - 1:
                    delay = self.retry_backoff[min(attempt, len(self.retry_backoff) - 1)]
                    logger.warning(
                        "lexicon_request_retry",
                        attempt=attempt + 1,
                        delay=delay,
                        error=str(e),
                    )
                    await asyncio.sleep(delay)
                continue

        # All retries exhausted. Wrap transport-layer failures (most
        # commonly httpx.ConnectError when Lexicon isn't deployed) into
        # ExternalServiceError so the cortex global handler maps it to
        # HTTP 503 + a typed envelope. Leaving last_error as a raw
        # httpx exception fell through to the 500 path with a full
        # stack trace in operator logs.
        if last_error:
            raise ExternalServiceError(
                service_name="Lexicon",
                reason=f"Lexicon unreachable: {last_error}",
            ) from last_error
        msg = "Request failed with unknown error"
        raise ExternalServiceError(service_name="Lexicon", reason=msg)

    async def get(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make GET request.

        Args:
            endpoint: API endpoint.
            params: Query parameters.

        Returns:
            JSON response.
        """
        return await self._request("GET", endpoint, params=params)

    async def post(
        self,
        endpoint: str,
        *,
        data: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make POST request.

        Args:
            endpoint: API endpoint.
            data: JSON request body.
            params: Query parameters.

        Returns:
            JSON response.
        """
        return await self._request("POST", endpoint, json_data=data, params=params)

    # Authentication methods

    async def login(self, username: str, password: str) -> AuthConfig:
        """Authenticate with lexicon and return auth config.

        The caller is responsible for persisting the returned AuthConfig.

        Args:
            username: Lexicon username.
            password: Lexicon password.

        Returns:
            AuthConfig with tokens.

        Raises:
            LexiconClientError: On authentication failure.
        """
        response = await self.post(
            "/auth/login",
            data={"username": username, "password": password},
        )

        self.auth = AuthConfig(
            token=response.get("token"),
            refresh_token=response.get("refresh_token"),
            expires_at=response.get("expires_at"),
            username=username,
        )

        logger.debug("lexicon_login_success", username=username)
        return self.auth

    def logout(self) -> None:
        """Clear authentication."""
        self.auth = AuthConfig()

    # Device Authorization Flow (RFC 8628)

    async def request_device_code(
        self,
        client_id: str = "chaoscypher-cli",
        scope: str = "read write",
    ) -> DeviceCodeResponse:
        """Request device and user codes for device authorization flow.

        This initiates OAuth 2.0 Device Authorization Grant (RFC 8628).
        The returned codes are used to complete authentication:
        1. Display verification_uri and user_code to user
        2. User opens URL in browser and enters code
        3. Poll with poll_device_token() until user completes auth

        Args:
            client_id: OAuth client identifier.
            scope: Requested OAuth scopes.

        Returns:
            DeviceCodeResponse with codes and URLs.

        Raises:
            LexiconClientError: On request failure.

        Example:
            device = await client.request_device_code()
            print(f"Visit {device.verification_uri}")
            print(f"Enter code: {device.user_code}")
        """
        response = await self.post(
            "/auth/device/code",
            data={"client_id": client_id, "scope": scope},
        )

        logger.info(
            "device_code_requested",
            verification_uri=response.get("verification_uri"),
        )

        return DeviceCodeResponse.from_dict(response)

    async def poll_device_token(
        self,
        device_code: str,
        client_id: str = "chaoscypher-cli",
        *,
        timeout: float | None = None,
        interval: float = 5.0,
        on_pending: Any | None = None,
    ) -> AuthConfig:
        """Poll for device token after user completes browser authentication.

        Continuously polls the token endpoint until:
        - User completes authentication (returns AuthConfig)
        - Device code expires (raises LexiconClientError)
        - Timeout is reached (raises LexiconClientError)

        Args:
            device_code: Device code from request_device_code().
            client_id: OAuth client identifier.
            timeout: Maximum seconds to poll (None = use expires_in).
            interval: Seconds between poll attempts.
            on_pending: Optional callback called on each pending poll.

        Returns:
            AuthConfig with access and refresh tokens.

        Raises:
            LexiconClientError: On expired code, denied access, or timeout.

        Example:
            device = await client.request_device_code()
            # ... user completes auth in browser ...
            auth = await client.poll_device_token(device.device_code)
        """
        import time

        start_time = time.time()
        max_time = timeout or 900  # 15 minutes default

        while True:
            elapsed = time.time() - start_time
            if elapsed > max_time:
                raise LexiconClientError(
                    status_code=408,
                    message="Device authorization timed out",
                    details={"elapsed": elapsed, "timeout": max_time},
                )

            try:
                response = await self.post(
                    "/auth/device/token",
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": client_id,
                    },
                )

                # Success! User completed authentication
                self.auth = AuthConfig(
                    token=response.get("access_token"),
                    refresh_token=response.get("refresh_token"),
                    expires_at=response.get("expires_at"),
                    username=response.get("username"),
                )

                logger.info(
                    "device_auth_success",
                    username=self.auth.username,
                )

                return self.auth

            except LexiconClientError as e:
                error_code = e.details.get("error", "")

                if error_code == "authorization_pending":
                    # User hasn't completed auth yet - keep polling
                    if on_pending:
                        on_pending()
                    await asyncio.sleep(interval)
                    continue

                if error_code == "slow_down":
                    # Server wants us to slow down
                    interval = min(interval + 5, 30)
                    await asyncio.sleep(interval)
                    continue

                if error_code == "expired_token":
                    raise LexiconClientError(
                        status_code=410,
                        message="Device code expired. Please restart login.",
                        details={"error": error_code},
                    ) from e

                if error_code == "access_denied":
                    raise LexiconClientError(
                        status_code=403,
                        message="Access denied by user.",
                        details={"error": error_code},
                    ) from e

                # Unknown error - re-raise
                raise

    # Package operations

    async def search(
        self,
        query: str = "",
        *,
        page: int = 1,
        limit: int = 20,
        sort_by: str = "relevance",
        is_public: bool | None = None,
        owner_id: str | None = None,
        conformance_class: str | None = None,
    ) -> tuple[list[PackageInfo], int]:
        """Search for packages on lexicon.

        Args:
            query: Search query string (empty returns all).
            page: Page number (1-indexed).
            limit: Results per page (max 100).
            sort_by: Sort order (relevance, stars, downloads, newest, updated, name).
            is_public: Filter by visibility.
            owner_id: Filter by owner ID.
            conformance_class: Filter by CCX conformance class (CCX 3.0).

        Returns:
            Tuple of (list of matching packages, total count).
        """
        params: dict[str, Any] = {
            "q": query,
            "type": "repositories",
            "page": page,
            "limit": limit,
            "sort": sort_by,
        }
        if is_public is not None:
            params["isPublic"] = str(is_public).lower()
        if owner_id:
            params["ownerId"] = owner_id
        if conformance_class:
            params["conformanceClass"] = conformance_class

        response = await self.get("/search", params=params)
        data = response.get("data", {})
        hits = data.get("hits", [])
        total = data.get("total", len(hits))
        return [PackageInfo.from_dict(p) for p in hits], total

    async def get_package_info(
        self,
        owner_username: str,
        repo_name: str,
        version: str | None = None,
    ) -> PackageInfo:
        """Get package metadata.

        Note: This searches for the package by name since there's no direct
        info endpoint. For download, use download() directly.

        Args:
            owner_username: Package owner's username.
            repo_name: Repository/package name.
            version: Optional version (not used for search).

        Returns:
            Package metadata.
        """
        # Search for this specific package
        packages, _ = await self.search(
            query=repo_name,
            limit=100,
        )

        # Find exact match
        for pkg in packages:
            if pkg.owner_username == owner_username and pkg.name == repo_name:
                return pkg

        raise LexiconClientError(
            status_code=404,
            message="Package not found",
            details={"owner": owner_username, "name": repo_name},
        )

    async def download(
        self,
        owner_username: str,
        repo_name: str,
        version: str = "latest",
    ) -> bytes:
        """Download package archive from lexicon.

        Args:
            owner_username: Package owner's username.
            repo_name: Repository/package name.
            version: Version tag (e.g., v1.0.0, 1.0.0, or latest).

        Returns:
            Archive bytes (.ccx content).

        Raises:
            LexiconClientError: On download failure.
        """
        download_url = f"{self.base_url}/packages/{owner_username}/{repo_name}/{version}"

        headers = {}
        if self.auth.is_authenticated:
            headers["Authorization"] = f"Bearer {self.auth.token}"

        # follow_redirects=False intentionally: the server can otherwise
        # 302-redirect package downloads to an arbitrary URL (cloud-metadata,
        # localhost services, etc.) and the response body would land on disk
        # as a "package" to process. If a future Lexicon server needs
        # cross-host redirects we should add manual per-hop URL validation.
        client = self._get_download_client()
        response = await client.get(download_url, headers=headers)

        if response.status_code == 404:
            raise LexiconClientError(
                status_code=404,
                message="Package or version not found",
                details={
                    "owner": owner_username,
                    "name": repo_name,
                    "version": version,
                },
            )

        if response.status_code == 429:
            raise LexiconClientError(
                status_code=429,
                message="Rate limit exceeded",
                details={},
            )

        response.raise_for_status()
        return response.content

    async def upload(
        self,
        archive_data: bytes,
        *,
        public: bool = True,
        message: str | None = None,
    ) -> UploadResult:
        """Upload a CCX 3.0 package archive to the Lexicon hub.

        Under the CCX 3.0 upload contract the hub accepts the multipart
        ``package`` field (Content-Type ``application/zip`` or
        ``application/vnd.ccx+zip``) and processes the package
        asynchronously, returning ``202`` with the standard data envelope
        ``{"data": {jobId, status, message}}`` rather than the final
        ``PackageInfo``. Callers poll the hub job endpoint for completion and
        then fetch package metadata via :meth:`get_package_info`.

        Args:
            archive_data: Archive bytes (.ccx content).
            public: Make package publicly visible.
            message: Optional upload/commit message.

        Returns:
            The queued upload job envelope (``job_id`` / ``status`` /
            ``message``).

        Raises:
            LexiconClientError: On upload failure or validation error.
        """
        if not self.auth.is_authenticated:
            raise LexiconClientError(
                status_code=401,
                message="Authentication required",
                details={"action": "upload"},
            )

        headers = self._get_headers()
        del headers["Content-Type"]  # Let httpx set multipart boundary

        upload_client = self._get_upload_client()
        # Content-Type application/zip is accepted by the CCX 3.0 hub
        # (application/vnd.ccx+zip is also accepted). Field name "package"
        # and filename "package.ccx" are pinned by the upload contract.
        files = {"package": ("package.ccx", archive_data, "application/zip")}
        data: dict[str, str] = {"public": str(public).lower()}
        if message:
            data["message"] = message

        # NOTE(lexicon-ccx-3.0): the pinned hub contract is the repo-scoped
        # endpoint POST /repositories/{repoId}/package. This client does not
        # currently carry a repoId (push is by archive only), so we keep the
        # existing flat /packages path until the hub exposes a repo-resolution
        # step. Field name, MIME, filename and the async job-envelope response
        # are already aligned to CCX 3.0. Tracked in internal/TODO.md
        # (§ CCX 3.0 migration P2, Lexicon hub upload-contract pairing).
        response = await upload_client.post(
            f"{self.base_url}/packages",
            files=files,
            data=data,
            headers=headers,
        )

        if response.status_code >= 400:
            try:
                error_data = response.json()
                err_message = error_data.get("message", "Upload failed")
                details = error_data.get("details", {})
            except Exception:
                err_message = "Upload failed"
                details = {}

            raise LexiconClientError(
                status_code=response.status_code,
                message=err_message,
                details=details,
            )

        # CCX 3.0: the hub returns 202 with the standard envelope
        # ``{"data": {jobId, status, message}}`` — unwrap ``data``. Fall back
        # to the top level so an older/bare ``{jobId, ...}`` body still parses.
        body = response.json()
        envelope = body.get("data") if isinstance(body, dict) else None
        if not isinstance(envelope, dict):
            envelope = body
        return UploadResult.from_dict(envelope)


__all__ = [
    "AuthConfig",
    "LexiconClient",
    "LexiconClientError",
    "PackageInfo",
    "UploadResult",
]
