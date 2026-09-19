# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph snapshot build handler for operations queue.

Provides ``handle_build_graph_snapshot`` async function that builds a
GraphBreakdown via ``BuildGraphSnapshotService`` and, when the build
covers the whole database (``source_ids is None``), persists the result
via ``GraphSnapshotRepository``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import ValidationError


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.services.graph.snapshot.models import GraphBreakdown


logger = structlog.get_logger(__name__)


async def handle_build_graph_snapshot(
    data: dict[str, Any],
    adapter: SqliteAdapter,
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Build a GraphBreakdown snapshot. Persists if whole-DB, returns inline if source-filtered.

    Args:
        data: Operation payload.  Keys:

            - ``database_name`` (str, required): database to aggregate.
            - ``source_ids`` (list[str] | None, optional): restrict to
              specific sources.  ``None`` or absent means whole-DB.
            - ``title`` (str | None, optional): display title passed
              through to the model.

        adapter: Connected SqliteAdapter for graph queries and engine access.
        metadata: Optional task metadata (unused, reserved for future use).
        task_id: Optional task ID for structured log correlation.

    Returns:
        ``{"success": True, "breakdown": <model_dump>}`` on success, or
        ``{"success": False, "error": <message>}`` on failure.

    Raises:
        ValidationError: If ``data['database_name']`` is missing or not a string.

    Behavior:
        - ``source_ids=None`` → build full snapshot, persist via
          ``GraphSnapshotRepository.upsert``, return dict payload.
        - ``source_ids`` given → build scoped snapshot, skip persistence,
          return dict payload (used by export path).

    """
    try:
        database_name = data.get("database_name")
        if not database_name or not isinstance(database_name, str):
            raise ValidationError(
                "data['database_name'] is required and must be a non-empty string",
                field="database_name",
            )

        source_ids: list[str] | None = data.get("source_ids")
        title: str | None = data.get("title")

        from chaoscypher_core.adapters.sqlite import SqliteAdapter
        from chaoscypher_core.adapters.sqlite.engine import get_engine
        from chaoscypher_core.adapters.sqlite.repos import GraphSnapshotRepository
        from chaoscypher_core.services.graph.snapshot.build_service import (
            BuildGraphSnapshotService,
        )

        engine = get_engine(adapter.db_path)

        def _build_and_persist() -> tuple[GraphBreakdown, bool]:
            """Run the six aggregates and the upsert off the event loop.

            Deliberately builds a private adapter rather than reusing the
            one passed in: the worker's adapter is process-wide and shared
            with every sibling handler, and ``SafeSession`` is not
            thread-safe, so offloading it would trade an event-loop stall
            for cross-thread session corruption. Same pattern, and the same
            reason, as ``operations/workflows/orchestrator.py``.

            It is built from ``adapter.db_path`` rather than through
            ``get_sqlite_adapter``, which re-resolves the path from settings
            and so would silently open a different database than the one
            this handler was handed. The engine is cached per path, so the
            extra adapter only costs a session.
            """
            build_adapter = SqliteAdapter(db_path=adapter.db_path)
            build_adapter.connect()
            try:
                built = BuildGraphSnapshotService.from_adapter(build_adapter).build(
                    database_name, source_ids, title
                )
            finally:
                build_adapter.disconnect()
            if source_ids is not None:
                return built, False
            GraphSnapshotRepository(engine).upsert(built)
            return built, True

        # ``build()`` is six sequential whole-database aggregates and
        # ``upsert()`` a sync write; awaiting them inline pinned the Neuron
        # event loop — every ops slot, the pollers, the heartbeat refresher
        # and the reconciler — for the whole build, on every post-commit
        # refresh.
        breakdown, persisted = await asyncio.to_thread(_build_and_persist)

        logger.info(
            "graph_snapshot_built",
            database_name=database_name,
            source_count=len(breakdown.sources),
            node_count=breakdown.stats.total_nodes,
            persisted=persisted,
            task_id=task_id,
        )

        return {"success": True, "breakdown": breakdown.model_dump(mode="json")}

    except Exception as e:
        logger.exception(
            "graph_snapshot_build_failed",
            error_type=type(e).__name__,
            error_message=str(e),
            task_id=task_id,
        )
        return {"success": False, "error": str(e)}
