# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Snapshot API Endpoints.

GET  /api/v1/graph/snapshot         - Return current graph breakdown (204 if none).
                                      Detects staleness and enqueues a background
                                      rebuild automatically when stale.
POST /api/v1/graph/snapshot/refresh - Enqueue a fresh snapshot build (202).
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Response, status

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.constants import OP_BUILD_GRAPH_SNAPSHOT, QUEUE_OPERATIONS
from chaoscypher_core.database import get_sqlite_adapter
from chaoscypher_core.ports.storage_graph_snapshot import SnapshotStalenessInfo
from chaoscypher_core.queue import queue_client
from chaoscypher_cortex.features.graph_snapshot.models import GraphBreakdown
from chaoscypher_cortex.features.graph_snapshot.service import GraphSnapshotFeatureService
from chaoscypher_cortex.shared.api.responses import COMMON_ERROR_RESPONSES
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername
from chaoscypher_cortex.shared.kernel import BulkResponse


logger = structlog.get_logger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


def get_snapshot_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> GraphSnapshotFeatureService:
    """Dependency: return a :class:`GraphSnapshotFeatureService` for the current DB."""
    adapter = get_sqlite_adapter(database_name=settings.current_database)
    return GraphSnapshotFeatureService(adapter)


# ---------------------------------------------------------------------------
# Staleness helpers
# ---------------------------------------------------------------------------


def _is_stale(
    staleness: SnapshotStalenessInfo,
    live_node_count: int,
    *,
    now: datetime,
    settings: Settings,
) -> bool:
    """Return True if the snapshot is stale by age or node-count drift.

    Args:
        staleness: Lightweight metadata from the snapshot store.
        live_node_count: Current node count from the live database.
        now: The current UTC time (injected for testability).
        settings: App settings (provides staleness thresholds).

    Returns:
        ``True`` when the snapshot should be rebuilt.

    """
    age = now - staleness.generated_at
    if age > timedelta(seconds=settings.graph_snapshot.staleness_threshold_seconds):
        return True
    drift = abs(live_node_count - staleness.node_count) / max(staleness.node_count, 1)
    return drift > settings.graph_snapshot.count_drift_threshold


async def _enqueue_refresh(database_name: str, settings: Settings, trigger: str) -> None:
    """Best-effort background enqueue; never raises.

    Logs a warning on failure so staleness errors are visible without
    breaking the response to the caller.

    Args:
        database_name: Database to rebuild the snapshot for.
        settings: App settings (used for queue priority).
        trigger: Human-readable reason label for observability.

    """
    try:
        await queue_client.enqueue_task(
            queue=QUEUE_OPERATIONS,
            operation=OP_BUILD_GRAPH_SNAPSHOT,
            data={"database_name": database_name},
            priority=settings.priorities.background,
            metadata={
                "operation_type": OP_BUILD_GRAPH_SNAPSHOT,
                "trigger": trigger,
            },
        )
    except Exception as exc:
        logger.warning(
            "graph_snapshot_refresh_enqueue_failed",
            trigger=trigger,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=GraphBreakdown,
    responses={
        204: {"description": "No snapshot exists yet"},
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_snapshot(
    _: CurrentUsername,
    svc: Annotated[GraphSnapshotFeatureService, Depends(get_snapshot_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GraphBreakdown | Response:
    """Return the latest pre-computed graph breakdown.

    If no snapshot exists, a build is enqueued automatically and ``204 No
    Content`` is returned.  If a snapshot exists but is stale (older than
    1 hour or node count has drifted by more than 10%), a background rebuild
    is enqueued while the stale data is returned immediately so the caller is
    never blocked.

    **Returns:**
    - ``200`` with a ``GraphBreakdown`` body when a snapshot is available.
    - ``204 No Content`` when no snapshot has been built yet.

    Use ``POST /api/v1/graph/snapshot/refresh`` to trigger a manual build.
    """
    database_name = settings.current_database

    staleness = svc.get_staleness_info(database_name)
    if staleness is None:
        # No snapshot yet — enqueue a build and return 204.
        await _enqueue_refresh(database_name, settings, trigger="no_snapshot")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    live_count = svc.get_live_node_count(database_name)
    now_utc = datetime.now(UTC)
    if _is_stale(staleness, live_count, now=now_utc, settings=settings):
        await _enqueue_refresh(database_name, settings, trigger="stale")

    breakdown = svc.get_current(database_name)
    if breakdown is None:
        # Race: snapshot disappeared between staleness check and full read.
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return breakdown


@router.post(
    "/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BulkResponse,
    responses={**COMMON_ERROR_RESPONSES},
)
async def refresh_snapshot(
    _: CurrentUsername,
    settings: Annotated[Settings, Depends(get_settings)],
) -> BulkResponse:
    """Enqueue a graph snapshot rebuild.

    The rebuild runs asynchronously in Neuron.  Use
    ``GET /api/v1/queue/tasks/{task_id}`` to track progress.

    **Returns:**
    - ``202 Accepted`` with ``task_id``, ``status``, and ``message``.
    """
    task_id = await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation=OP_BUILD_GRAPH_SNAPSHOT,
        data={"database_name": settings.current_database},
        priority=settings.priorities.background,
        metadata={
            "operation_type": OP_BUILD_GRAPH_SNAPSHOT,
            "trigger": "manual_refresh",
        },
    )
    return BulkResponse(
        task_id=task_id,
        status="queued",
        message="Graph snapshot refresh queued",
    )
