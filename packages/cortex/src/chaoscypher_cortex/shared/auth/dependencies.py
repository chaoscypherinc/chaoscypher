# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Auth dependencies — nginx auth_request model.

With nginx-gated auth, every `/api/` request that reaches FastAPI has already
passed auth at the edge. Nginx runs a subrequest to `/api/v1/auth/verify`,
which (on success) sets ``X-Auth-User`` on the forwarded request. The app
trusts the header only when nginx also supplies the deployment-local
``X-Auth-Edge-Token`` marker, so direct/header-spoofed requests fail closed.

Single-user model: the user is always the local operator. Feature endpoints
use ``CurrentUsername`` — a plain annotated string that represents the
authenticated username without any admin/superuser distinction.

Dev/test fallback: when `settings.dev_mode=True` and no header is present,
we return a synthetic "dev" user. This is NEVER enabled in production — it
exists only for `uvicorn` direct runs and local pytest test clients that
don't have nginx in front.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, status

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.shared.api.responses import ErrorDetail
from chaoscypher_cortex.shared.utils.client_ip import client_ip


logger = structlog.get_logger(__name__)

_AUTH_USER_HEADER = "X-Auth-User"
_DEV_USERNAME = "dev"


def _read_auth_header(request: Request) -> str | None:
    """Pull X-Auth-User off the request, normalizing whitespace.

    Returns None when the header is absent or blank.
    """
    raw = request.headers.get(_AUTH_USER_HEADER)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def read_auth_header_optional(request: Request) -> str | None:
    """Public façade over ``_read_auth_header`` for callers that must not raise.

    Endpoints that are intentionally public (e.g. ``GET /api/v1/health``) but
    want to branch on authentication status use this instead of importing the
    private helper.  Returns the ``X-Auth-User`` value when present and
    non-blank, otherwise ``None``.

    Args:
        request: The current FastAPI ``Request`` object.

    Returns:
        The username string when the header is present, otherwise ``None``.
    """
    return _read_auth_header(request)


def _configured_edge_token(settings: Settings) -> str | None:
    """Return the shared edge-auth token from settings/env or token file."""
    token = settings.local_auth.edge_auth_token
    if token is not None:
        return token.get_secret_value()

    token_path = settings.local_auth.edge_auth_token_path
    try:
        if token_path.exists():
            value = token_path.read_text(encoding="utf-8").strip()
            return value or None
    except OSError:
        logger.warning("edge_auth_token_read_failed", path=str(token_path))
    return None


def has_valid_edge_token(headers: Mapping[str, str], settings: Settings) -> bool:
    """Check whether nginx marked the identity header as edge-verified."""
    expected = _configured_edge_token(settings)
    if not expected:
        logger.error("edge_auth_token_not_configured")
        return False

    presented = headers.get(settings.local_auth.edge_auth_header)
    if not presented:
        return False
    return hmac.compare_digest(presented.strip(), expected)


def get_current_username(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """Return the authenticated username from nginx-set ``X-Auth-User``.

    Raises 401 in production if the header is missing (nginx should have
    already rejected; if we reach here without the header, something is
    misrouted). In ``dev_mode``, returns "dev" to allow uvicorn-direct runs.

    Every 401 is also recorded in the process-local ``AuthFailureTracker`` so
    operators can diagnose silent 401 storms via ``GET /api/v1/health/auth``.
    """
    from chaoscypher_cortex.features.local_auth.auth_failure_tracker import tracker

    header = _read_auth_header(request)
    if header:
        if settings.dev_mode or has_valid_edge_token(request.headers, settings):
            return header
        logger.warning(
            "untrusted_auth_header_rejected",
            path=request.url.path,
            client=client_ip(request),
        )
        tracker.record_failure()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorDetail(
                code="AUTH_REQUIRED",
                message="Authentication required",
            ).model_dump(),
        )
    if settings.dev_mode:
        return _DEV_USERNAME
    tracker.record_failure()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=ErrorDetail(
            code="AUTH_REQUIRED",
            message="Authentication required",
        ).model_dump(),
    )


# ============================================================================
# Type Aliases — use in endpoint signatures for concise annotations.
# ============================================================================

# Canonical annotation: a plain username string from nginx X-Auth-User.
CurrentUsername = Annotated[str, Depends(get_current_username)]
