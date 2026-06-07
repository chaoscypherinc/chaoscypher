# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Top-level HTTP middleware registered by the app factory.

Module-level async functions (not inner closures) so tests can import them
and call them directly with a fake Request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from fastapi.responses import JSONResponse


if TYPE_CHECKING:
    from fastapi import Request


logger = structlog.get_logger(__name__)


# Paths that remain reachable while the DB is blocked on a tier-2
# migration. Covers (a) the upgrade namespace so the maintenance UI can
# list/apply/rollback and (b) /health so monitoring stays functional.
# All /api/v1/auth/* endpoints bypass the gate via prefix match below —
# nginx owns auth gating, so the maintenance middleware has no reason to
# 503 on auth checks (doing so breaks login from the maintenance page).
_UPGRADE_GATE_ALLOWLIST = (
    "/api/v1/health",
    "/api/v1/upgrade/pending",
    "/api/v1/upgrade/apply",
    "/api/v1/upgrade/rollback",
)
_UPGRADE_GATE_ALLOWLIST_PREFIXES = ("/api/v1/auth/",)

# Routes that legitimately accept large bodies. The body-size middleware
# skips the per-route ``max_request_body_mb`` cap on these paths; each
# upload route enforces ``max_upload_bytes`` (default 5 GB) explicitly
# during streaming. New upload endpoints MUST be added here.
_UPLOAD_PATHS = frozenset(
    {
        "/api/v1/sources",
        "/api/v1/sources/batch",
        "/api/v1/lexicon/upload",
        "/api/v1/exports/import",
    }
)


_BODY_BEARING_METHODS = frozenset({"POST", "PUT", "PATCH"})


async def enforce_body_size_limit(request: Request, call_next: Any) -> Any:  # noqa: PLR0911
    """Reject requests that exceed the configured body size limit.

    Defense-in-depth alongside Nginx's ``client_max_body_size``. Two paths:

    * **Content-Length present** — reject before the body is buffered.
    * **Content-Length absent** (``Transfer-Encoding: chunked``, HTTP/2
      streaming, malformed client) — stream the body in this middleware,
      counting bytes, and reject as soon as the cap is crossed. The
      buffered body is then cached on the request so the downstream
      handler can read it back.

    Upload routes (see ``_UPLOAD_PATHS``) are exempted — they enforce
    ``max_upload_bytes`` themselves during streaming.

    Browser clients (``Accept: text/html``) get a branded HTML page; API
    clients get the canonical ``UnifiedErrorResponse`` JSON envelope.
    """
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_cortex.shared.errors import negotiated_error_response

    if request.url.path in _UPLOAD_PATHS:
        return await call_next(request)

    settings = get_settings()
    max_bytes = settings.batching.max_request_body_mb * 1024 * 1024

    def _too_large() -> Any:
        """Build the 413 'request body too large' negotiated error response."""
        max_mb = settings.batching.max_request_body_mb
        return negotiated_error_response(
            request,
            status_code=413,
            error_code="body_too_large",
            json_payload={
                "error": "body_too_large",
                "message": f"Request body too large (max {max_mb}MB).",
                "details": {"max_mb": max_mb},
            },
            html_kwargs={
                "title": "That request is too large",
                "lead": f"This server rejects request bodies larger than {max_mb} MB.",
                "details": [("Limit", f"{max_mb} MB")],
                "why": (
                    "Request bodies are capped as a defense-in-depth measure "
                    "against memory exhaustion. Upload endpoints have their "
                    "own, higher per-file cap."
                ),
                "fix": [
                    "Try the upload again with a smaller payload.",
                    "An admin can raise `batching.max_request_body_mb` in Settings.",
                ],
                "http_label": "HTTP 413 Payload Too Large",
            },
        )

    def _bad_content_length() -> Any:
        """Build the 400 'invalid Content-Length' negotiated error response."""
        return negotiated_error_response(
            request,
            status_code=400,
            error_code="bad_content_length",
            json_payload={
                "error": "bad_content_length",
                "message": "Invalid Content-Length header.",
                "details": None,
            },
            html_kwargs={
                "title": "Malformed request",
                "lead": "The Content-Length header on this request isn't a valid integer.",
                "http_label": "HTTP 400 Bad Request",
            },
        )

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            request_bytes = int(content_length)
        except ValueError:
            return _bad_content_length()
        if request_bytes < 0:
            return _bad_content_length()
        if request_bytes > max_bytes:
            return _too_large()
        return await call_next(request)

    if request.method not in _BODY_BEARING_METHODS:
        return await call_next(request)

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            return _too_large()
        chunks.append(chunk)

    # Stash the buffered body on the request so Starlette's BaseHTTPMiddleware
    # serves it back to the downstream handler via ``wrapped_receive`` —
    # the stream we just drained cannot be replayed.
    request._body = b"".join(chunks)  # noqa: SLF001
    return await call_next(request)


async def upgrade_gate_middleware(request: Request, call_next: Any) -> Any:
    """Return 503 on /api/* when the DB is blocked on a tier-2 migration.

    Lets the Interface's top-level effect redirect to /maintenance instead
    of seeing cryptic errors from feature endpoints that assume the schema
    is up to date.
    """
    from chaoscypher_core.app_config import get_settings

    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    if path in _UPGRADE_GATE_ALLOWLIST or path.startswith(_UPGRADE_GATE_ALLOWLIST_PREFIXES):
        return await call_next(request)

    try:
        from chaoscypher_core.database.engine import get_db_path
        from chaoscypher_core.database.migrations.state import get_upgrade_state

        settings = get_settings()
        db_path = get_db_path(settings.current_database)
        state = get_upgrade_state(db_path)
    except Exception:
        # If we can't even read the state (e.g. DB not yet initialized),
        # let the request through — the downstream handler will surface
        # whatever's actually wrong.
        return await call_next(request)

    if not state.ready:
        return JSONResponse(
            status_code=503,
            content={
                "error": "upgrade_required",
                "message": state.message,
                "blocked_on": state.blocked_on,
                "retry_at": "/api/v1/upgrade/pending",
            },
            headers={"Retry-After": "5"},
        )
    return await call_next(request)
