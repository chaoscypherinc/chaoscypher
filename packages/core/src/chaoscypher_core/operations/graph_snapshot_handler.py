# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph snapshot build handler for operations queue.

Provides ``handle_build_graph_snapshot`` async function that builds a
GraphBreakdown via ``BuildGraphSnapshotService`` and, when the build
covers the whole database (``source_ids is None``), persists the result
via ``GraphSnapshotRepository``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import ValidationError


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter


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

        from chaoscypher_core.adapters.sqlite.engine import get_engine
        from chaoscypher_core.adapters.sqlite.repos import GraphSnapshotRepository
        from chaoscypher_core.services.graph.snapshot.build_service import (
            BuildGraphSnapshotService,
        )

        breakdown = BuildGraphSnapshotService.from_adapter(adapter).build(
            database_name, source_ids, title
        )

        persisted = False
        if source_ids is None:
            engine = get_engine(adapter.db_path)
            GraphSnapshotRepository(engine).upsert(breakdown)
            persisted = True

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
