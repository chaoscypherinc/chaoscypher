# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""FastAPI application factory for Cortex.

`create_app()` builds a fully-configured FastAPI instance: middleware,
CORS, local auth, exception handlers, feature routers, static files.
Schema-only mode short-circuits session-secret disk I/O so the Dockerfile
types-builder stage can extract the OpenAPI schema without side effects.
"""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from chaoscypher_cortex.boot import APP_VERSION
from chaoscypher_cortex.lifespan import lifespan_full
from chaoscypher_cortex.middleware import (
    enforce_body_size_limit,
    upgrade_gate_middleware,
)


logger = structlog.get_logger(__name__)


def _load_or_create_session_secret(secret_path: Path) -> bytes:
    """Read the session HMAC secret; auto-generate on first run if missing.

    In Docker, the entrypoint seeds this before the app starts; this
    fallback exists so local uvicorn dev runs work without extra steps.
    """
    if secret_path.exists():
        data = secret_path.read_bytes()
        if len(data) < 32:
            msg = f"Session secret at {secret_path} is {len(data)} bytes; must be >=32"
            raise RuntimeError(msg)
        return data

    logger.warning(
        "session_secret_auto_generated",
        path=str(secret_path),
        detail="Generated 32-byte session secret (first-boot fallback).",
    )
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    data = os.urandom(32)
    secret_path.write_bytes(data)
    with suppress(AttributeError, OSError):
        # Windows: chmod has limited semantics; acceptable for dev.
        secret_path.chmod(0o600)
    return data


def _resolve_session_secret(secret_path: Path, *, schema_only: bool) -> bytes:
    """Return a session HMAC secret for app construction.

    When ``schema_only`` is True the caller is constructing the app solely
    to extract the OpenAPI schema (Dockerfile types-builder stage); return
    a dummy zero-filled 32-byte value so no disk write happens inside the
    build container. The resulting app is not usable for serving requests.
    """
    if schema_only:
        return b"\x00" * 32
    return _load_or_create_session_secret(secret_path)


def create_app(*, schema_only: bool = False) -> FastAPI:  # noqa: PLR0915 — app factory wires every middleware + router; splitting would just hide statements behind helper calls.
    """Build and return the Cortex FastAPI application.

    All FastAPI setup (middleware registration, CORS, local auth wiring,
    exception handlers, router inclusion, static-file mount) happens inside
    this factory so importing any module has no side effects beyond logger
    and boot-time constants.

    Args:
        schema_only: When True, build an app suitable only for OpenAPI schema
            extraction (Dockerfile types-builder stage). Uses dummy
            session-secret bytes and skips any disk writes. The returned app
            is structurally complete (all routes registered) but not usable
            for serving requests.

    Returns:
        A configured FastAPI instance ready to serve (or to dump its OpenAPI
        schema via ``app.openapi()``).
    """
    from fastapi import HTTPException
    from fastapi.exceptions import RequestValidationError

    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.exceptions import ChaosCypherException
    from chaoscypher_core.services.local_auth import CredentialsFile
    from chaoscypher_cortex.api.v1.router import create_api_router
    from chaoscypher_cortex.features.local_auth import LocalAuthService
    from chaoscypher_cortex.features.local_auth import build_router as build_local_auth_router
    from chaoscypher_cortex.features.mcp.api import get_mcp_transport
    from chaoscypher_cortex.shared.api.errors import (
        chaoscypher_exception_handler,
        global_exception_handler,
        http_exception_handler,
        validation_exception_handler,
    )
    from chaoscypher_cortex.shared.middleware.adapter_cleanup import AdapterCleanupMiddleware
    from chaoscypher_cortex.shared.middleware.correlation import CorrelationIdMiddleware
    from chaoscypher_cortex.shared.middleware.host_header import HostHeaderCheckMiddleware
    from chaoscypher_cortex.shared.middleware.rate_limit import RateLimitMiddleware
    from chaoscypher_cortex.shared.middleware.security_headers import SecurityHeadersMiddleware

    settings = get_settings()

    if settings.dev_mode and not schema_only and os.getenv("CHAOSCYPHER_ALLOW_DEV_MODE") != "1":
        msg = (
            "dev_mode=True detected at app construction. This disables auth checks "
            "in MCP and falls back to a synthetic 'dev' user — never safe to ship. "
            "If running uvicorn directly for local development, set "
            "CHAOSCYPHER_ALLOW_DEV_MODE=1. In production, remove dev_mode: true "
            "from settings.yaml instead."
        )
        logger.critical("dev_mode_refused", detail=msg)
        raise SystemExit(msg)

    enable_docs = os.getenv("ENABLE_API_DOCS", "false").lower() == "true"

    app = FastAPI(
        title="Chaos Cypher Knowledge Engine API",
        description="Full REST API for knowledge graph management with AI-powered research capabilities",
        version=APP_VERSION,
        lifespan=lifespan_full,
        docs_url="/docs" if enable_docs else None,
        redoc_url="/redoc" if enable_docs else None,
        openapi_url="/openapi.json" if enable_docs else None,
    )

    # ---- Body Size / Upgrade-Gate Middleware --------------------------

    app.middleware("http")(enforce_body_size_limit)
    app.middleware("http")(upgrade_gate_middleware)

    # ---- CORS Middleware ----------------------------------------------

    cors_origins = settings.cors.allowed_origins
    if not cors_origins:
        # Self-hosted default: only the dev interface (localhost:3000) is
        # treated as a credentialed cross-origin caller. Everything else is
        # same-origin via nginx and doesn't need CORS at all. Operators with
        # a custom dev port set settings.cors.allowed_origins explicitly.
        # Defensive copy — settings.cors.dev_fallback_origins is a list[str].
        default_origins = list(settings.cors.dev_fallback_origins)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=default_origins,
            allow_credentials=settings.cors.allow_credentials,
            allow_methods=settings.cors.allow_methods,
            allow_headers=settings.cors.allow_headers,
        )
        logger.info("cors_default_dev_origins", origins=default_origins)
    elif settings.cors.allow_credentials and "*" in cors_origins:
        msg = (
            "CORS misconfiguration: allow_credentials=True with wildcard origin is "
            "invalid per the CORS spec. Set explicit origins in settings.yaml."
        )
        logger.critical("cors_insecure_configuration", detail=msg)
        raise SystemExit(msg)
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=settings.cors.allow_credentials,
            allow_methods=settings.cors.allow_methods,
            allow_headers=settings.cors.allow_headers,
        )
        logger.info("cors_configured", origins=cors_origins)

    # ---- Shared Middleware --------------------------------------------

    app.add_middleware(RateLimitMiddleware, settings=settings.rate_limit)
    app.add_middleware(CorrelationIdMiddleware)
    # Security headers on every response — covers dev / non-nginx deployments.
    app.add_middleware(SecurityHeadersMiddleware)
    # Host-header check blocks DNS rebinding. The settings_provider closure
    # re-reads Settings each request so admin PATCHes to
    # security.allow_external_access / security.allowed_hosts take effect
    # immediately without a container restart. Pre-setup (setup_completed=
    # False), the middleware itself bypasses the check so the operator can
    # reach /setup from any device on their LAN to complete first-run.
    app.add_middleware(
        HostHeaderCheckMiddleware,
        settings_provider=get_settings,
    )
    # AdapterCleanupMiddleware must be added after CorrelationIdMiddleware
    # (Starlette executes middleware in reverse order, so this runs outermost
    # — it initializes adapter tracking before any handler and cleans up
    # after)
    app.add_middleware(AdapterCleanupMiddleware)

    # ---- Local Auth (nginx auth_request model) ------------------------

    session_secret = _resolve_session_secret(
        settings.local_auth.session_secret_path, schema_only=schema_only
    )
    credentials = CredentialsFile(settings.local_auth.credentials_path)
    local_auth_service = LocalAuthService(
        credentials=credentials,
        session_secret=session_secret,
        cookie_ttl_seconds=settings.local_auth.cookie_ttl_seconds,
    )
    app.include_router(
        build_local_auth_router(
            local_auth_service,
            cookie_name=settings.local_auth.cookie_name,
            cookie_secure=settings.local_auth.cookie_secure,
        )
    )

    # ---- Exception Handlers -------------------------------------------

    # Unified error envelope: every handler produces {error, message, details?}.
    # See errors.py docstrings. Order matters less since FastAPI dispatches by
    # exception type, but the more-specific handlers must be registered before
    # the Exception catch-all.
    app.add_exception_handler(ChaosCypherException, chaoscypher_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)

    # ---- Feature Routers ----------------------------------------------

    app.include_router(create_api_router())
    # Mount MCP Streamable HTTP transport as ASGI sub-app
    app.mount("/api/v1/mcp", get_mcp_transport())

    # Public settings endpoint (auth-exempt — SPA needs it pre-login).
    # Carries its own /api/v1/settings prefix so it mounts directly on the app.
    from chaoscypher_cortex.features.settings_public import router as settings_public_router

    app.include_router(settings_public_router)

    # ---- Health Check -------------------------------------------------

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Liveness probe — confirms the Cortex process is up.

        Returns immediately with no dependency checks. Used by multi-container
        Docker HEALTHCHECK and ``cc-cortex health``.

        For a full readiness check (Valkey, workers, LLM, search, graph),
        use ``GET /api/v1/health`` instead.
        """
        return {"status": "healthy"}

    # ---- Static File Serving (Production) -----------------------------

    if os.getenv("APP_NAME") == "web_ui":
        static_dir = os.getenv("STATIC_DIR") or settings.paths.static_dir
        if os.path.exists(static_dir):
            # Serve static assets (JS/CSS bundles)
            app.mount("/assets", StaticFiles(directory=f"{static_dir}/assets"), name="assets")

            # Serve index.html for all other routes (SPA routing). If the
            # requested path matches an actual file in the static dir (e.g.
            # logo.png, favicon.ico), serve that file directly.
            @app.get("/{full_path:path}", response_model=None)
            def serve_spa(full_path: str) -> FileResponse | JSONResponse:
                """Serve SPA for all non-API routes."""
                if full_path.startswith("api/"):
                    # /api/* paths that no router claimed: return a real 404
                    # with the canonical UnifiedErrorResponse envelope, not
                    # a 200 with a stub body (which broke both the status
                    # contract and the envelope contract).
                    return JSONResponse(
                        status_code=404,
                        content={
                            "error": "NOT_FOUND",
                            "message": f"No route matches /{full_path}",
                            "details": None,
                        },
                    )

                # Serve actual static files (logo.png, favicon.ico, etc.)
                resolved = Path(static_dir, full_path).resolve()
                static_real = Path(static_dir).resolve()
                if full_path and str(resolved).startswith(str(static_real)) and resolved.is_file():
                    return FileResponse(resolved)

                return FileResponse(f"{static_dir}/index.html")

    return app
