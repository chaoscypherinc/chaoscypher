# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Lexicon API Endpoints - Thin layer over core LexiconService.

Provides REST API endpoints for lexicon operations. All business logic
and models are imported from chaoscypher_core.services.lexicon.

Endpoints:
    Auth:
        POST /auth/device   - Initiate device authorization flow
        POST /auth/poll     - Poll for device token
        POST /auth/login    - Username/password login
        POST /auth/token    - Direct token authentication
        POST /auth/logout   - Clear credentials
        GET  /auth/status   - Get current auth status

    Packages:
        GET  /search              - Search packages
        GET  /r/{owner}/{name}    - Get package info
        POST /upload              - Upload package
"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.services.lexicon import (
    DictLexiconStorage,
    LexiconAuthResponse,
    LexiconAuthStatus,
    LexiconClientError,
    LexiconDeviceCodeRequest,
    LexiconDeviceCodeResponse,
    LexiconLoginRequest,
    LexiconPackageInfo,
    LexiconPollRequest,
    LexiconSearchRequest,
    LexiconSearchResponse,
    LexiconService,
    LexiconTokenRequest,
    LexiconUploadRequest,
)
from chaoscypher_core.utils.url_safety import validate_url_safety
from chaoscypher_cortex.shared.api.dependencies import (
    LimitParam,
)
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    RATE_LIMIT_RESPONSE,
    SERVICE_UNAVAILABLE_RESPONSE,
    ErrorDetail,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


logger = structlog.get_logger(__name__)

router = APIRouter()

# The request models default ``lexicon_url`` to the operator-configured
# registry (env ``LEXICON_URL``). That default is trusted server config, not
# attacker input, so only a *client-supplied override* needs the strict SSRF
# check — short-circuiting it also avoids a DNS lookup of the default on every
# auth call (and keeps the check offline-safe).
_TRUSTED_LEXICON_URL: str = LexiconDeviceCodeRequest.model_fields["lexicon_url"].default


# =============================================================================
# Factory Function
# =============================================================================


def _reject_unsafe_lexicon_url(lexicon_url: str) -> None:
    """Block a client-overridden ``lexicon_url`` that fails SSRF safety checks.

    ``request_device_code`` / ``poll_device_token`` / ``login`` build a
    ``LexiconClient`` against this URL and make outbound HTTP calls, so an
    attacker-supplied value (cloud-metadata IP, loopback service, non-HTTP
    scheme) is an SSRF vector. Mirrors the import-URL guard in
    ``features/sources/api.py``.

    Args:
        lexicon_url: The request's ``lexicon_url`` value.

    Raises:
        HTTPException: 400 when an overridden URL is not safe to fetch.

    """
    if lexicon_url == _TRUSTED_LEXICON_URL:
        return
    # Strict policy: block loopback/private/reserved in addition to cloud
    # metadata, closing the DNS-rebinding window for user-submitted URLs.
    if not validate_url_safety(lexicon_url, strict=True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(
                code="VALIDATION_FAILED",
                message=(
                    "lexicon_url is not allowed (blocked scheme, "
                    "private/loopback host, or cloud metadata endpoint)"
                ),
            ).model_dump(),
        )


def get_lexicon_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> LexiconService:
    """Factory for LexiconService with settings-based storage.

    Creates a LexiconService using DictLexiconStorage backed by
    the lexicon settings from settings.yaml.

    Args:
        settings: Application settings.

    Returns:
        Configured LexiconService instance.
    """
    # settings.lexicon.model_dump() unwraps SecretStr via the field_serializer
    # so token / refresh_token reach DictLexiconStorage as plaintext, matching
    # the contract documented on DictLexiconStorage.__init__. api_url is a
    # computed property, not a model field, so it must be patched in.
    storage_data = settings.lexicon.model_dump()
    storage_data["url"] = settings.lexicon.api_url  # Combined base URL + API path
    storage = DictLexiconStorage(storage_data)
    return LexiconService(storage)


def _handle_lexicon_error(e: LexiconClientError) -> HTTPException:
    """Convert LexiconClientError to HTTPException with a sanitized body.

    The upstream ``e.message`` and ``e.details`` may contain internal
    URLs, field names, or other server-side info. Log them at WARNING
    and return a sanitized envelope to the client.

    Args:
        e: Lexicon client error.

    Returns:
        HTTPException with appropriate status code and sanitized detail.
    """
    status_map = {
        401: 401,  # Unauthorized
        403: 403,  # Forbidden
        404: 404,  # Not Found
        408: 408,  # Request Timeout (device code expired)
        410: 408,  # Gone -> Request Timeout
    }
    http_status = status_map.get(e.status_code, 503)  # Default to Service Unavailable
    logger.warning(
        "lexicon_upstream_error",
        status_code=e.status_code,
        upstream_message=e.message,
        upstream_details=e.details,
    )
    return HTTPException(
        status_code=http_status,
        detail=ErrorDetail(
            code="LEXICON_UPSTREAM_ERROR",
            message="Upstream lexicon request failed",
        ).model_dump(),
    )


# =============================================================================
# Auth Endpoints
# =============================================================================


@router.post(
    "/auth/device",
    response_model=LexiconDeviceCodeResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_LIMIT_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def request_device_code(
    _: CurrentUsername,
    request: LexiconDeviceCodeRequest,
    service: Annotated[LexiconService, Depends(get_lexicon_service)],
) -> LexiconDeviceCodeResponse:
    """Initiate device authorization flow.

    Starts OAuth 2.0 Device Authorization Grant. Returns codes for
    user to complete authentication in a browser.

    Args:
        request: Device code request parameters.
        service: Lexicon service instance.

    Returns:
        Device code response with verification URL and codes.
    """
    _reject_unsafe_lexicon_url(request.lexicon_url)
    try:
        return await service.request_device_code(request)
    except LexiconClientError as e:
        logger.exception(
            "device_code_request_failed",
            lexicon_url=request.lexicon_url,
        )
        raise _handle_lexicon_error(e) from e


@router.post(
    "/auth/poll",
    response_model=LexiconAuthResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_LIMIT_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def poll_device_token(
    _: CurrentUsername,
    request: LexiconPollRequest,
    service: Annotated[LexiconService, Depends(get_lexicon_service)],
) -> LexiconAuthResponse:
    """Poll for device token.

    Single poll to check if user has completed browser authentication.
    Returns immediately with success or pending status.

    Args:
        request: Poll request with device code.
        service: Lexicon service instance.

    Returns:
        Auth response with success status.
    """
    _reject_unsafe_lexicon_url(request.lexicon_url)
    try:
        return await service.poll_device_token(request)
    except LexiconClientError as e:
        logger.exception(
            "device_token_poll_failed",
            lexicon_url=request.lexicon_url,
        )
        raise _handle_lexicon_error(e) from e


@router.post(
    "/auth/login",
    response_model=LexiconAuthResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_LIMIT_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def login(
    _: CurrentUsername,
    request: LexiconLoginRequest,
    service: Annotated[LexiconService, Depends(get_lexicon_service)],
) -> LexiconAuthResponse:
    """Login with username and password.

    Authenticates with lexicon using credentials.

    Args:
        request: Login request with credentials.
        service: Lexicon service instance.

    Returns:
        Auth response with success status.
    """
    _reject_unsafe_lexicon_url(request.lexicon_url)
    try:
        return await service.login(request)
    except LexiconClientError as e:
        logger.exception(
            "lexicon_login_failed",
            lexicon_url=request.lexicon_url,
            username=request.username,
        )
        raise _handle_lexicon_error(e) from e


@router.post(
    "/auth/token",
    response_model=LexiconAuthResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def set_token(
    _: CurrentUsername,
    request: LexiconTokenRequest,
    service: Annotated[LexiconService, Depends(get_lexicon_service)],
) -> LexiconAuthResponse:
    """Set token directly.

    Used for CI/automation scenarios where token is provided directly.

    Args:
        request: Token request with JWT.
        service: Lexicon service instance.

    Returns:
        Auth response with success status.
    """
    return service.set_token(request)


@router.post(
    "/auth/logout",
    response_model=LexiconAuthResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def logout(
    _: CurrentUsername,
    service: Annotated[LexiconService, Depends(get_lexicon_service)],
) -> LexiconAuthResponse:
    """Clear lexicon credentials.

    Returns:
        Auth response confirming logout.
    """
    return service.logout()


@router.get(
    "/auth/status",
    response_model=LexiconAuthStatus,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def get_auth_status(
    _: CurrentUsername,
    service: Annotated[LexiconService, Depends(get_lexicon_service)],
) -> LexiconAuthStatus:
    """Get current lexicon authentication status.

    Returns:
        Current auth status including username and token presence.
    """
    return service.get_auth_status()


# =============================================================================
# Package Endpoints
# =============================================================================


@router.get(
    "/search",
    response_model=LexiconSearchResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_LIMIT_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def search_packages(
    _: CurrentUsername,
    service: Annotated[LexiconService, Depends(get_lexicon_service)],
    limit: LimitParam,
    query: str = Query("", description="Search query string (empty returns all)"),
    page: int = Query(1, ge=1, description="Page number"),
    sort_by: str = Query("downloads", description="Sort by field"),
    is_public: bool | None = Query(None, description="Filter by visibility"),
    owner_id: str | None = Query(None, description="Filter by owner ID"),
    package_type: str | None = Query(None, description="Filter by package type"),
) -> LexiconSearchResponse:
    """Search for packages on lexicon.

    Args:
        query: Search query string (empty returns all packages).
        page: Page number (1-indexed).
        limit: Results per page.
        sort_by: Sort results (relevance, stars, downloads, newest, updated, name).
        is_public: Filter by visibility.
        owner_id: Filter by owner ID.
        package_type: Filter by package type.
        service: Lexicon service instance.

    Returns:
        Search response with matching packages.
    """
    try:
        request = LexiconSearchRequest(
            query=query,
            page=page,
            limit=limit,
            sort_by=sort_by,
            is_public=is_public,
            owner_id=owner_id,
            package_type=package_type,
        )
        return await service.search(request)
    except LexiconClientError as e:
        logger.exception("lexicon_search_failed", query=query)
        raise _handle_lexicon_error(e) from e


@router.get(
    "/r/{owner}/{name}",
    response_model=LexiconPackageInfo,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_LIMIT_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def get_package_info(
    owner: str,
    name: str,
    _: CurrentUsername,
    service: Annotated[LexiconService, Depends(get_lexicon_service)],
) -> LexiconPackageInfo:
    """Get package metadata.

    Args:
        owner: Package owner's username.
        name: Repository/package name.
        service: Lexicon service instance.

    Returns:
        Package metadata.
    """
    try:
        return await service.get_package_info(owner, name)
    except LexiconClientError as e:
        logger.exception(
            "lexicon_get_package_failed",
            owner=owner,
            name=name,
        )
        raise _handle_lexicon_error(e) from e


class LexiconImportRequest(BaseModel):
    """Request body for queuing a Lexicon package import."""

    owner_username: str = Field(..., description="Lexicon package owner username.")
    repo_name: str = Field(..., description="Lexicon package repository name.")
    version: str = Field("latest", description="Package version tag (defaults to latest).")


class LexiconImportResponse(BaseModel):
    """Response for a queued Lexicon package import."""

    message: str
    task_id: str
    status: str
    owner_username: str
    repo_name: str
    version: str


@router.post(
    "/import",
    response_model=LexiconImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def import_package(
    _: CurrentUsername,
    request: LexiconImportRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> LexiconImportResponse:
    """Queue a Lexicon package import.

    Enqueues a background ``lexicon_import`` operation that downloads the
    package and imports it into the current database. Returns immediately
    with a ``task_id`` the client can poll via ``/queue/tasks/{task_id}``.
    """
    from chaoscypher_core.constants import QUEUE_OPERATIONS
    from chaoscypher_core.queue import queue_client

    package_key = f"{request.owner_username}/{request.repo_name}"

    task_id = await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation="lexicon_import",
        data={
            "owner_username": request.owner_username,
            "repo_name": request.repo_name,
            "version": request.version,
            "database_name": settings.current_database,
        },
        priority=settings.priorities.background,
        metadata={"operation_type": "lexicon_import", "package": package_key},
    )

    return LexiconImportResponse(
        message=f"Import of {package_key} queued. Check Queue Monitor for status.",
        task_id=task_id,
        status="queued",
        owner_username=request.owner_username,
        repo_name=request.repo_name,
        version=request.version,
    )


@router.post(
    "/upload",
    response_model=LexiconPackageInfo,
    status_code=status.HTTP_201_CREATED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
        **RATE_LIMIT_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def upload_package(
    _: CurrentUsername,
    service: Annotated[LexiconService, Depends(get_lexicon_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: UploadFile = File(..., description="Package archive (.ccx)"),
    public: bool = Query(True, description="Make package publicly visible"),
    message: str | None = Query(None, description="Upload message"),
) -> LexiconPackageInfo:
    """Upload package archive to lexicon.

    Requires authentication. The archive must be a valid .ccx file.

    Args:
        file: Package archive file.
        public: Whether package is publicly visible.
        message: Optional upload message.
        service: Lexicon service instance.
        settings: Application settings.

    Returns:
        Uploaded package info.
    """
    max_upload_bytes = settings.batching.max_upload_bytes
    try:
        content = await file.read(max_upload_bytes + 1)
        if len(content) > max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=ErrorDetail(
                    code="PAYLOAD_TOO_LARGE",
                    message=f"Package file too large (max {max_upload_bytes // 1024 // 1024} MB)",
                ).model_dump(),
            )
        request = LexiconUploadRequest(public=public, message=message)
        return await service.upload(content, request)
    except LexiconClientError as e:
        logger.exception(
            "lexicon_upload_failed",
            filename=file.filename,
            public=public,
        )
        raise _handle_lexicon_error(e) from e
