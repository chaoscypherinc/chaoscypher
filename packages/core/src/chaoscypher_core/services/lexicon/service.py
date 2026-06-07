# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Lexicon Service - High-level lexicon operations with credential management.

Provides a service layer that wraps LexiconClient with:
- Automatic credential persistence
- Pydantic model conversion
- Consistent error handling

Used by both CLI and Cortex for lexicon operations.

Example:
    from chaoscypher_core.services.lexicon import (
        LexiconService,
        FileLexiconStorage,
        LexiconSearchRequest,
    )

    storage = FileLexiconStorage()
    service = LexiconService(storage)

    # Search packages
    results = await service.search(LexiconSearchRequest(query="medical"))
    for pkg in results.packages:
        print(f"{pkg.name} v{pkg.version}")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from pydantic import SecretStr

from chaoscypher_core.services.lexicon.client import AuthConfig, LexiconClient, PackageInfo
from chaoscypher_core.services.lexicon.models import (
    LexiconAuthConfig,
    LexiconAuthResponse,
    LexiconAuthStatus,
    LexiconDeviceCodeRequest,
    LexiconDeviceCodeResponse,
    LexiconDownloadRequest,
    LexiconLoginRequest,
    LexiconPackageInfo,
    LexiconPollRequest,
    LexiconSearchRequest,
    LexiconSearchResponse,
    LexiconTokenRequest,
    LexiconUploadRequest,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.lexicon.storage import LexiconCredentialStorage

logger = structlog.get_logger(__name__)


def _auth_config_to_pydantic(auth: AuthConfig) -> LexiconAuthConfig:
    """Convert dataclass AuthConfig to Pydantic LexiconAuthConfig.

    Args:
        auth: Dataclass auth config.

    Returns:
        Pydantic auth config.
    """
    return LexiconAuthConfig(
        token=SecretStr(auth.token) if auth.token else None,
        refresh_token=SecretStr(auth.refresh_token) if auth.refresh_token else None,
        expires_at=auth.expires_at,
        username=auth.username,
    )


def _pydantic_to_auth_config(auth: LexiconAuthConfig) -> AuthConfig:
    """Convert Pydantic LexiconAuthConfig to dataclass AuthConfig.

    Args:
        auth: Pydantic auth config.

    Returns:
        Dataclass auth config.
    """
    return AuthConfig(
        token=auth.token.get_secret_value() if auth.token else None,
        refresh_token=auth.refresh_token.get_secret_value() if auth.refresh_token else None,
        expires_at=auth.expires_at,
        username=auth.username,
    )


def _package_info_to_pydantic(info: PackageInfo) -> LexiconPackageInfo:
    """Convert dataclass PackageInfo to Pydantic LexiconPackageInfo.

    Args:
        info: Dataclass package info.

    Returns:
        Pydantic package info.
    """
    return LexiconPackageInfo(
        id=info.id,
        name=info.name,
        description=info.description,
        owner_username=info.owner_username,
        owner_name=info.owner_name,
        owner_id=info.owner_id,
        is_public=info.is_public,
        package_type=info.package_type,
        star_count=info.star_count,
        version_count=info.version_count,
        download_count=info.download_count,
        created_at=info.created_at,
        updated_at=info.updated_at,
    )


class LexiconService:
    """High-level hub operations with credential management.

    Wraps LexiconClient with:
    - Automatic credential persistence via storage protocol
    - Pydantic model input/output for type safety
    - Consistent error handling and logging

    This service is used by both CLI and Cortex for hub operations.
    The storage backend determines where credentials are persisted.

    Attributes:
        storage: Credential storage backend.

    Example:
        # CLI usage with file storage
        storage = FileCredentialStorage()
        service = LexiconService(storage)

        # Cortex usage with dict storage
        storage = DictCredentialStorage(settings.lexicon.model_dump())
        service = LexiconService(storage)
    """

    def __init__(self, storage: LexiconCredentialStorage) -> None:
        """Initialize lexicon service.

        Args:
            storage: Credential storage backend.
        """
        self.storage = storage

    def _get_client(self, lexicon_url: str | None = None) -> LexiconClient:
        """Create LexiconClient with stored credentials.

        Args:
            lexicon_url: Optional hub URL override.

        Returns:
            Configured LexiconClient instance.
        """
        creds = self.storage.load_credentials()
        auth = _pydantic_to_auth_config(creds) if creds else None
        url = lexicon_url or self.storage.get_lexicon_url()
        return LexiconClient(base_url=url, auth=auth)

    # =========================================================================
    # Auth Operations
    # =========================================================================

    async def request_device_code(
        self, request: LexiconDeviceCodeRequest
    ) -> LexiconDeviceCodeResponse:
        """Initiate device authorization flow.

        Starts OAuth 2.0 Device Authorization Grant. The response contains
        codes for the user to complete authentication in a browser.

        Args:
            request: Device code request parameters.

        Returns:
            Device code response with codes and verification URL.

        Raises:
            LexiconClientError: On request failure.
        """
        async with LexiconClient(base_url=request.lexicon_url) as client:
            response = await client.request_device_code(
                client_id=request.client_id,
                scope=request.scope,
            )

        logger.info(
            "device_code_requested",
            lexicon_url=request.lexicon_url,
            verification_uri=response.verification_uri,
        )

        return LexiconDeviceCodeResponse(
            device_code=response.device_code,
            user_code=response.user_code,
            verification_uri=response.verification_uri,
            verification_uri_complete=response.verification_uri_complete,
            expires_in=response.expires_in,
            interval=response.interval,
        )

    async def poll_device_token(self, request: LexiconPollRequest) -> LexiconAuthResponse:
        """Poll for device token (single poll, non-blocking).

        Performs a single poll to check if user has completed authentication.
        Returns immediately with success or pending status.

        For blocking poll until completion, use poll_device_token_blocking.

        Args:
            request: Poll request parameters.

        Returns:
            Auth response with success status.

        Raises:
            LexiconClientError: On request failure or access denied.
        """
        from chaoscypher_core.services.lexicon.client import LexiconClientError

        async with LexiconClient(base_url=request.lexicon_url) as client:
            try:
                # Single poll attempt
                response = await client.post(
                    "/auth/device/token",
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": request.device_code,
                        "client_id": request.client_id,
                    },
                )

                # Success - save credentials
                auth = LexiconAuthConfig(
                    token=response.get("access_token"),
                    refresh_token=response.get("refresh_token"),
                    expires_at=response.get("expires_at"),
                    username=response.get("username"),
                )

                self.storage.save_credentials(request.lexicon_url, auth)

                logger.info(
                    "device_auth_success",
                    lexicon_url=request.lexicon_url,
                    username=auth.username,
                )

                return LexiconAuthResponse(
                    success=True,
                    username=auth.username,
                    lexicon_url=request.lexicon_url,
                    message="Successfully authenticated",
                )

            except LexiconClientError as e:
                error_code = e.details.get("error", "")

                if error_code == "authorization_pending":
                    return LexiconAuthResponse(
                        success=False,
                        username=None,
                        lexicon_url=request.lexicon_url,
                        message="Authorization pending - user has not completed auth",
                    )
                if error_code == "slow_down":
                    return LexiconAuthResponse(
                        success=False,
                        username=None,
                        lexicon_url=request.lexicon_url,
                        message="Slow down - poll less frequently",
                    )
                # Re-raise for expired/denied errors
                raise

    async def poll_device_token_blocking(
        self,
        request: LexiconPollRequest,
        *,
        timeout: float | None = None,
        interval: float = 5.0,
    ) -> LexiconAuthResponse:
        """Poll for device token until completion (blocking).

        Continuously polls until user completes authentication or timeout.
        Use this for CLI interactive flows.

        Args:
            request: Poll request parameters.
            timeout: Maximum seconds to poll (None = 15 minutes).
            interval: Seconds between poll attempts.

        Returns:
            Auth response with success status.

        Raises:
            LexiconClientError: On expired code, denied access, or timeout.
        """
        async with LexiconClient(base_url=request.lexicon_url) as client:
            auth = await client.poll_device_token(
                device_code=request.device_code,
                client_id=request.client_id,
                timeout=timeout,
                interval=interval,
            )

        # Save credentials
        hub_auth = _auth_config_to_pydantic(auth)
        self.storage.save_credentials(request.lexicon_url, hub_auth)

        logger.info(
            "device_auth_success",
            lexicon_url=request.lexicon_url,
            username=auth.username,
        )

        return LexiconAuthResponse(
            success=True,
            username=auth.username,
            lexicon_url=request.lexicon_url,
            message="Successfully authenticated",
        )

    async def login(self, request: LexiconLoginRequest) -> LexiconAuthResponse:
        """Login with username and password.

        Authenticates with hub using credentials and saves the returned token.

        Args:
            request: Login request with credentials.

        Returns:
            Auth response with success status.

        Raises:
            LexiconClientError: On authentication failure.
        """
        async with LexiconClient(base_url=request.lexicon_url) as client:
            auth = await client.login(request.username, request.password.get_secret_value())

        # Save credentials
        hub_auth = _auth_config_to_pydantic(auth)
        self.storage.save_credentials(request.lexicon_url, hub_auth)

        logger.info(
            "login_success",
            lexicon_url=request.lexicon_url,
            username=auth.username,
        )

        return LexiconAuthResponse(
            success=True,
            username=auth.username,
            lexicon_url=request.lexicon_url,
            message="Successfully logged in",
        )

    def set_token(self, request: LexiconTokenRequest) -> LexiconAuthResponse:
        """Set token directly.

        Used for CI/automation scenarios where token is provided directly.

        Args:
            request: Token request with JWT.

        Returns:
            Auth response with success status.
        """
        auth = LexiconAuthConfig(
            token=request.token,
            username=request.username,
        )

        self.storage.save_credentials(request.lexicon_url, auth)

        logger.info(
            "token_set",
            lexicon_url=request.lexicon_url,
            username=request.username,
        )

        return LexiconAuthResponse(
            success=True,
            username=request.username,
            lexicon_url=request.lexicon_url,
            message="Token saved successfully",
        )

    def logout(self) -> LexiconAuthResponse:
        """Clear stored credentials.

        Returns:
            Auth response confirming logout.
        """
        lexicon_url = self.storage.get_lexicon_url()
        self.storage.clear_credentials()

        logger.info("logout_success")

        return LexiconAuthResponse(
            success=True,
            username=None,
            lexicon_url=lexicon_url,
            message="Successfully logged out",
        )

    def get_auth_status(self) -> LexiconAuthStatus:
        """Get current authentication status.

        Returns:
            Current auth status.
        """
        creds = self.storage.load_credentials()
        lexicon_url = self.storage.get_lexicon_url()

        return LexiconAuthStatus(
            authenticated=creds.is_authenticated if creds else False,
            username=creds.username if creds else None,
            lexicon_url=lexicon_url,
            token_present=creds is not None and creds.token is not None,
        )

    # =========================================================================
    # Package Operations
    # =========================================================================

    async def search(self, request: LexiconSearchRequest) -> LexiconSearchResponse:
        """Search for packages on lexicon.

        Args:
            request: Search request with query and filters.

        Returns:
            Search response with matching packages.

        Raises:
            LexiconClientError: On request failure.
        """
        client = self._get_client()
        async with client:
            packages, total = await client.search(
                query=request.query,
                page=request.page,
                limit=request.limit,
                sort_by=request.sort_by,
                is_public=request.is_public,
                owner_id=request.owner_id,
                package_type=request.package_type,
            )

        return LexiconSearchResponse(
            packages=[_package_info_to_pydantic(p) for p in packages],
            total=total,
            page=request.page,
            limit=request.limit,
        )

    async def get_package_info(self, owner_username: str, repo_name: str) -> LexiconPackageInfo:
        """Get package metadata.

        Args:
            owner_username: Package owner's username.
            repo_name: Repository/package name.

        Returns:
            Package metadata.

        Raises:
            LexiconClientError: On request failure or package not found.
        """
        client = self._get_client()
        async with client:
            info = await client.get_package_info(owner_username, repo_name)

        return _package_info_to_pydantic(info)

    async def download(self, request: LexiconDownloadRequest) -> bytes:
        """Download package archive.

        Args:
            request: Download request with owner, repo name, and version.

        Returns:
            Archive bytes (.ccx content).

        Raises:
            LexiconClientError: On download failure or package not found.
        """
        client = self._get_client()
        async with client:
            archive = await client.download(
                owner_username=request.owner_username,
                repo_name=request.repo_name,
                version=request.version,
            )

        logger.info(
            "package_downloaded",
            package=f"{request.owner_username}/{request.repo_name}",
            version=request.version,
            size=len(archive),
        )

        return archive

    async def upload(
        self, archive_data: bytes, request: LexiconUploadRequest
    ) -> LexiconPackageInfo:
        """Upload package archive.

        Args:
            archive_data: Archive bytes (.ccx content).
            request: Upload request with metadata.

        Returns:
            Uploaded package info.

        Raises:
            LexiconClientError: On upload failure or validation error.
        """
        client = self._get_client()
        async with client:
            info = await client.upload(
                archive_data=archive_data,
                public=request.public,
                message=request.message,
            )

        logger.info(
            "package_uploaded",
            package=info.name,
            version=info.version,
            public=request.public,
        )

        return _package_info_to_pydantic(info)


__all__ = ["LexiconService"]
