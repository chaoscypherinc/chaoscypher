# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Snapshot Feature Service.

Cortex read-path wrapper around :class:`GraphSnapshotRepository`.
Intentionally thin — exposes staleness info and live node counts for the
staleness-based auto-refresh logic in the GET endpoint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.ports.storage_graph_snapshot import SnapshotStalenessInfo
    from chaoscypher_core.services.graph.snapshot.models import GraphBreakdown


class GraphSnapshotFeatureService:
    """Cortex service wrapping GraphSnapshotRepository read path."""

    def __init__(self, adapter: SqliteAdapter) -> None:
        """Initialise with a SQLite adapter.

        Args:
            adapter: SqliteAdapter instance for the current database.

        """
        self._adapter = adapter

    def get_current(self, database_name: str) -> GraphBreakdown | None:
        """Return the latest snapshot for ``database_name``, or None.

        Args:
            database_name: Database whose snapshot to retrieve.

        Returns:
            Deserialised ``GraphBreakdown`` or ``None`` when no snapshot exists.

        """
        from chaoscypher_core.adapters.sqlite.engine import get_engine
        from chaoscypher_core.adapters.sqlite.repos.graph_snapshot import (
            GraphSnapshotRepository,
        )

        engine = get_engine(self._adapter.db_path)
        repo = GraphSnapshotRepository(engine)
        return repo.get_current(database_name)

    def get_staleness_info(self, database_name: str) -> SnapshotStalenessInfo | None:
        """Pass-through to the storage port's cheap staleness check.

        Returns lightweight metadata (``generated_at``, ``node_count``,
        ``edge_count``) without deserialising the full JSON payload.

        Args:
            database_name: Database whose staleness info to retrieve.

        Returns:
            ``SnapshotStalenessInfo`` or ``None`` when no snapshot exists.

        """
        from chaoscypher_core.adapters.sqlite.engine import get_engine
        from chaoscypher_core.adapters.sqlite.repos.graph_snapshot import (
            GraphSnapshotRepository,
        )

        engine = get_engine(self._adapter.db_path)
        repo = GraphSnapshotRepository(engine)
        return repo.get_staleness_info(database_name)

    def get_live_node_count(self, database_name: str) -> int:
        """COUNT(*) of graph_nodes for the given database — cheap staleness check.

        Uses ``GraphBreakdownQueryRepository.count_all_nodes`` so that the
        adapter access stays in one place and no raw SQL appears in the service.

        Args:
            database_name: Database to count nodes for.

        Returns:
            Total number of graph nodes currently stored for the database.

        """
        from chaoscypher_core.adapters.sqlite.repos.graph_breakdown import (
            GraphBreakdownQueryRepository,
        )

        assert self._adapter.session is not None, "adapter must be connected"
        repo = GraphBreakdownQueryRepository(self._adapter.session)
        return repo.count_all_nodes(database_name)
