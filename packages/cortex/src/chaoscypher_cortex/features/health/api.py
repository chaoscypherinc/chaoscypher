# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Health API.

Consolidated system health endpoint that aggregates checks
from all subsystems into a single response.
"""

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.features.health.models import HealthCheckResponse
from chaoscypher_cortex.features.health.service import HealthService
from chaoscypher_cortex.shared.api import safe_create
from chaoscypher_cortex.shared.api.responses import (
    COMMON_ERROR_RESPONSES,
    SERVICE_UNAVAILABLE_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import (
    has_valid_edge_token,
    read_auth_header_optional,
)
from chaoscypher_cortex.shared.database.session import get_current_session


if TYPE_CHECKING:
    from chaoscypher_cortex.shared.health.probes import HealthProbe

router = APIRouter()


def _build_counts_dep(session: Any, settings: Settings) -> Any:
    """Build a CountsService dependency from shared repositories and adapters."""
    from chaoscypher_core.database import get_sqlite_adapter
    from chaoscypher_core.repo_factories import get_graph_repository
    from chaoscypher_core.services.graph.engine.stats import CountsService

    adapter = get_sqlite_adapter(database_name=settings.current_database)
    graph_repo = get_graph_repository(session, settings.current_database)
    return CountsService(
        graph_repository=graph_repo,
        sources_repository=adapter,
        database_name=settings.current_database,
    )


def _get_queue_valkey_client() -> Any:
    """Get the raw async Valkey client from the queue singleton."""
    from chaoscypher_core.queue import queue_client

    return queue_client.client


def _get_search_repository(settings: Settings) -> Any:
    """Get the singleton SearchRepository for reindex status checks."""
    from chaoscypher_core.repo_factories import get_search_repository

    return get_search_repository(database_name=settings.current_database)


def _build_search_probe(session: Any, settings: Settings) -> Any:
    """Build a SearchHealthProbe using the search feature's own factory.

    Constructs the ``RepositoryBundle`` the search service expects, then
    delegates to ``features/search.api.get_search_service`` so that the
    probe is wrapped around the same SearchService that real API requests
    would see. Health never imports SearchService directly — it only holds
    the resulting probe object.

    Args:
        session: Current request's database session.
        settings: Application settings.

    Returns:
        A ``SearchHealthProbe`` wrapping the freshly built SearchService.
    """
    from chaoscypher_cortex.features.search.api import get_search_service
    from chaoscypher_cortex.features.search.probe import SearchHealthProbe
    from chaoscypher_cortex.shared.repositories.bundle import RepositoryBundle

    bundle = RepositoryBundle(session, settings)
    search_service = get_search_service(repos=bundle, settings=settings)
    return SearchHealthProbe(search_service=search_service)


def get_health_service(
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthService:
    """Create HealthService with current dependencies.

    Uses FastAPI DI for session management (not manual session creation).
    Gathers queue client, sibling-feature probes, and counts service. Each
    dependency is created via ``safe_create`` so that a failure in one
    subsystem does not prevent the health endpoint from responding.

    Sibling-feature probes (e.g. SearchHealthProbe) are constructed here in
    the composition root and injected into the service. The health service
    itself has no knowledge of their internals.

    Args:
        session: Database session from FastAPI DI.
        settings: Application settings.

    Returns:
        Configured HealthService instance.
    """
    probes: list[HealthProbe] = []
    search_probe = safe_create(_build_search_probe, session, settings)
    if search_probe is not None:
        probes.append(search_probe)

    return HealthService(
        settings=settings,
        queue_client=safe_create(_get_queue_valkey_client),
        counts_service=safe_create(_build_counts_dep, session, settings),
        search_repository=safe_create(_get_search_repository, settings),
        probes=probes,
    )


@router.get(
    "/health/auth",
    tags=["health"],
)
async def get_auth_health(request: Request) -> dict[str, object]:
    """Diagnostic endpoint for nginx auth misconfiguration.

    Returns the current state of the process-local auth failure counter. This
    endpoint is **intentionally public** (no auth required) — if auth were
    required, the endpoint could not be used to diagnose auth being broken.

    When nginx's ``auth_request`` directive is misconfigured, ``X-Auth-User``
    may not arrive at Cortex, producing silent 401 storms. Poll this endpoint
    to detect and diagnose the problem without needing a working auth session.

    **Returns:**

    - ``x_auth_user_present``: whether ``X-Auth-User`` is present in this
      specific request (i.e., nginx is forwarding the header right now).
    - ``recent_failed_attempts``: count of 401s issued in the last 5 minutes.
    - ``last_failure_at``: ISO-8601 UTC timestamp of the most recent 401, or
      ``null`` if none has occurred yet in this process.
    """
    from chaoscypher_cortex.features.local_auth.auth_failure_tracker import tracker

    return {
        "x_auth_user_present": "X-Auth-User" in request.headers,
        "recent_failed_attempts": tracker.window_count(),
        "last_failure_at": tracker.last_at(),
    }


@router.get(
    "/health",
    response_model=HealthCheckResponse,
    response_model_exclude_none=True,
    responses={
        **COMMON_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def get_system_health(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[HealthService, Depends(get_health_service)],
) -> HealthCheckResponse:
    """Get consolidated system health status.

    This endpoint is intentionally public so Docker HEALTHCHECK can reach it
    without authentication.  To prevent fingerprinting the deployed LLM stack
    and queue worker topology from the LAN, **full probe details are only
    returned when the caller is authenticated** (i.e. when nginx has forwarded
    a valid ``X-Auth-User`` + ``X-Auth-Edge-Token`` pair, or ``dev_mode`` is
    enabled).  Unauthenticated callers receive only ``{healthy, status}``.

    Checks all subsystems (authenticated only):
    - Ollama connectivity and version
    - Chat, extraction, and vision model availability
    - Embedding model status
    - Queue (Valkey) connectivity
    - LLM and operations worker health
    - Search index stats
    - Graph database stats

    **Note:** This is separate from the Docker healthcheck at GET /health.
    Response is cached for 5 seconds to prevent excessive subsystem queries.

    **Returns:**
    - healthy: Overall health (false if any critical check fails)
    - status: "ok" or "degraded" (always present)
    - checks: Per-subsystem health check results (authenticated callers only)
    """
    header = read_auth_header_optional(request)
    is_authed = bool(header) and (
        settings.dev_mode or has_valid_edge_token(request.headers, settings)
    )
    return await service.check_health(detailed=is_authed)
