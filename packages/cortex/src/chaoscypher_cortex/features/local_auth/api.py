# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Local auth HTTP routes.

Public routes: /status, /setup, /login, /logout
Internal: /verify (nginx auth_request target)
Authenticated: /me, /password, /username, /keys

Auth is satisfied by either a session cookie (set on login/setup) OR an
``Authorization: Bearer <api_key>`` header. Nginx's auth_request subrequest
calls /verify; the endpoint returns 200 with X-Auth-User on success, 401 on
failure.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from chaoscypher_core.services.local_auth import (
    ApiKeyNotFound,
    CredentialsNotInitialized,
    InvalidPassword,
    InvalidSessionCookie,
    UsernameMismatch,
)
from chaoscypher_cortex.features.local_auth.models import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyListItem,
    AuthStatusResponse,
    ChangePasswordRequest,
    ChangeUsernameRequest,
    LoginRequest,
    SetupRequest,
    UserResponse,
)
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
)


if TYPE_CHECKING:
    from chaoscypher_cortex.features.local_auth.service import LocalAuthService


_bearer = HTTPBearer(auto_error=False)


def build_router(  # noqa: C901, PLR0915
    service: LocalAuthService,
    *,
    cookie_name: str,
    cookie_secure: bool,
) -> APIRouter:
    """Return the configured APIRouter for the local-auth feature.

    This is a factory function — not a FastAPI dependency — because the
    service + cookie settings are resolved in ``main.py``'s lifespan and
    passed once at mount time.

    Args:
        service: The ``LocalAuthService`` orchestrator to wire into routes.
        cookie_name: Name of the session cookie to set/read.
        cookie_secure: Whether to mark the cookie ``Secure`` (HTTPS-only).

    Returns:
        An ``APIRouter`` mounted at ``/api/v1/auth`` with all auth routes.
    """
    router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

    def _set_cookie(response: Response, value: str) -> None:
        """Set the signed session cookie on the outgoing response."""
        response.set_cookie(
            key=cookie_name,
            value=value,
            httponly=True,
            secure=cookie_secure,
            samesite="strict",
            path="/",
        )

    def _clear_cookie(response: Response) -> None:
        """Clear the session cookie. Mirrors setter attrs so browsers honor the deletion."""
        response.delete_cookie(
            key=cookie_name,
            path="/",
            httponly=True,
            secure=cookie_secure,
            samesite="strict",
        )

    async def _resolve_username(
        request: Request,
        bearer: HTTPAuthorizationCredentials | None,
    ) -> str | None:
        """Resolve auth from cookie OR bearer token.

        Cookie wins when both are present. Returns ``None`` if the caller is
        not authenticated by either mechanism. The credential lookups touch
        bcrypt + file I/O, so they run in a thread to keep the event loop
        responsive.
        """
        cookie = request.cookies.get(cookie_name)
        if cookie:
            try:
                return await asyncio.to_thread(service.verify_session_cookie, cookie)
            except InvalidSessionCookie:
                pass
        if bearer and bearer.scheme.lower() == "bearer":
            matched = await asyncio.to_thread(service.verify_api_key, bearer.credentials)
            if matched:
                return await asyncio.to_thread(service.get_username)
        return None

    async def _require_username(
        request: Request,
        bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> str:
        """Return the authenticated username or raise 401."""
        username = await _resolve_username(request, bearer)
        if username is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        return username

    @router.get(
        "/status",
        response_model=AuthStatusResponse,
        responses={
            **COMMON_ERROR_RESPONSES,
        },
    )
    async def get_status(request: Request) -> AuthStatusResponse:
        """Return setup/auth state for the current caller."""
        cookie = request.cookies.get(cookie_name)
        return await asyncio.to_thread(service.status, session_cookie=cookie)

    @router.post(
        "/setup",
        response_model=UserResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            **COMMON_ERROR_RESPONSES,
            **CONFLICT_RESPONSE,
        },
    )
    async def setup(req: SetupRequest, response: Response) -> UserResponse:
        """First-run admin bootstrap. Returns 409 if already initialized."""
        try:
            cookie = await asyncio.to_thread(
                service.setup, req.username, req.password.get_secret_value()
            )
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail="already initialized") from exc
        _set_cookie(response, cookie)
        return UserResponse(username=req.username)

    @router.post(
        "/login",
        response_model=UserResponse,
        responses={
            **COMMON_ERROR_RESPONSES,
            **AUTH_ERROR_RESPONSES,
            **CONFLICT_RESPONSE,
        },
    )
    async def login(req: LoginRequest, response: Response) -> UserResponse:
        """Validate password and issue a session cookie."""
        try:
            cookie = await asyncio.to_thread(
                service.login, req.username, req.password.get_secret_value()
            )
        except (InvalidPassword, UsernameMismatch) as exc:
            raise HTTPException(status_code=401, detail="invalid credentials") from exc
        except CredentialsNotInitialized as exc:
            raise HTTPException(status_code=409, detail="setup required") from exc
        _set_cookie(response, cookie)
        return UserResponse(username=req.username)

    @router.post(
        "/logout",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={
            **COMMON_ERROR_RESPONSES,
        },
    )
    async def logout(response: Response) -> None:
        """Clear the session cookie and invalidate every outstanding session."""
        await asyncio.to_thread(service.logout)
        _clear_cookie(response)

    @router.get(
        "/verify",
        responses={
            **COMMON_ERROR_RESPONSES,
            **AUTH_ERROR_RESPONSES,
        },
    )
    async def verify(
        request: Request,
        response: Response,
        bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> Response:
        """Nginx ``auth_request`` target — 200 + X-Auth-User or 401."""
        username = await _resolve_username(request, bearer)
        if username is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        response.headers["X-Auth-User"] = username
        response.status_code = 200
        return response

    @router.get(
        "/me",
        response_model=UserResponse,
        responses={
            **COMMON_ERROR_RESPONSES,
            **AUTH_ERROR_RESPONSES,
        },
    )
    async def me(
        request: Request,
        bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> UserResponse:
        """Return the authenticated caller's username."""
        return UserResponse(username=await _require_username(request, bearer))

    @router.post(
        "/password",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={
            **COMMON_ERROR_RESPONSES,
            **AUTH_ERROR_RESPONSES,
        },
    )
    async def change_password(
        req: ChangePasswordRequest,
        response: Response,
        request: Request,
        bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> None:
        """Rotate the admin password. Clears cookie, forcing re-login."""
        username = await _require_username(request, bearer)
        try:
            await asyncio.to_thread(
                service.change_password,
                username,
                req.old_password.get_secret_value(),
                req.new_password.get_secret_value(),
            )
        except InvalidPassword as exc:
            raise HTTPException(status_code=403, detail="invalid password") from exc
        _clear_cookie(response)

    @router.post(
        "/username",
        response_model=UserResponse,
        responses={
            **COMMON_ERROR_RESPONSES,
            **AUTH_ERROR_RESPONSES,
        },
    )
    async def change_username(
        req: ChangeUsernameRequest,
        response: Response,
        request: Request,
        bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> UserResponse:
        """Rename the admin account and issue a fresh cookie for the new name."""
        old_username = await _require_username(request, bearer)
        try:
            new_cookie = await asyncio.to_thread(
                service.change_username,
                old_username,
                req.password.get_secret_value(),
                req.new_username,
            )
        except InvalidPassword as exc:
            raise HTTPException(status_code=403, detail="invalid password") from exc
        _set_cookie(response, new_cookie)
        return UserResponse(username=req.new_username)

    @router.post(
        "/keys",
        response_model=ApiKeyCreateResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            **COMMON_ERROR_RESPONSES,
            **AUTH_ERROR_RESPONSES,
        },
    )
    async def create_key(
        req: ApiKeyCreateRequest,
        request: Request,
        bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> ApiKeyCreateResponse:
        """Mint a new API key. Plaintext returned once — caller must store it."""
        await _require_username(request, bearer)
        return await asyncio.to_thread(service.create_api_key, req.name)

    @router.get(
        "/keys",
        response_model=list[ApiKeyListItem],
        responses={
            **COMMON_ERROR_RESPONSES,
            **AUTH_ERROR_RESPONSES,
        },
    )
    async def list_keys(
        request: Request,
        bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> list[ApiKeyListItem]:
        """List API keys (no hashes, no plaintext material)."""
        await _require_username(request, bearer)
        return await asyncio.to_thread(service.list_api_keys)

    @router.delete(
        "/keys/{key_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={
            **COMMON_ERROR_RESPONSES,
            **AUTH_ERROR_RESPONSES,
            **NOT_FOUND_RESPONSE,
        },
    )
    async def revoke_key(
        key_id: str,
        request: Request,
        bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> None:
        """Revoke the API key with the given id. 404 if not found."""
        await _require_username(request, bearer)
        try:
            await asyncio.to_thread(service.revoke_api_key, key_id)
        except ApiKeyNotFound as exc:
            raise HTTPException(status_code=404, detail="key not found") from exc

    return router
