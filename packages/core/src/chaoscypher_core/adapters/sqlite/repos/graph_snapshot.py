# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphSnapshotRepository — SQLite-backed implementation of GraphSnapshotStorageProtocol.

Concrete implementation of
:class:`chaoscypher_core.ports.storage_graph_snapshot.GraphSnapshotStorageProtocol`
backed by the SQLite adapter.  One row per database in the
``graph_snapshots`` table.

Pattern: engine-based (like ``SearchRepository``).  Each method opens its
own ``Session(self._engine)`` and commits before returning.  This is
intentional — the repo owns a full standalone transaction per call
(no external coordinator is expected for snapshot writes).
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

import structlog
from sqlmodel import Session

from chaoscypher_core.adapters.sqlite.models import GraphSnapshot
from chaoscypher_core.ports.storage_graph_snapshot import SnapshotStalenessInfo
from chaoscypher_core.services.graph.snapshot.models import GraphBreakdown


if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = structlog.get_logger(__name__)


class GraphSnapshotRepository:
    """SQLite-backed store for pre-computed GraphBreakdown snapshots.

    One row per database (``database_name`` is the PK).  Callers upsert
    after a graph build completes; the dashboard reads via
    ``get_current``; the staleness check reads via ``get_staleness_info``.
    """

    def __init__(self, engine: Engine) -> None:
        """Initialise with a SQLAlchemy engine pointing at the app database.

        Args:
            engine: SQLAlchemy engine (SQLite) that contains the
                ``graph_snapshots`` table.

        """
        self._engine = engine

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert(self, breakdown: GraphBreakdown) -> None:
        """Insert or replace the snapshot for ``breakdown.database_name``.

        Serialises the full ``GraphBreakdown`` to JSON and stores it in
        ``payload_json``.  Scalar summary columns (``node_count``,
        ``edge_count``, ``generated_at``) are derived from the breakdown
        so that ``get_staleness_info`` can read them without deserialising.

        Args:
            breakdown: The graph breakdown to persist.

        """
        with Session(self._engine) as session:
            row = session.get(GraphSnapshot, breakdown.database_name)
            if row is None:
                row = GraphSnapshot(
                    database_name=breakdown.database_name,
                    generated_at=breakdown.generated_at,
                    payload_json=breakdown.model_dump_json(),
                    node_count=breakdown.stats.total_nodes,
                    edge_count=breakdown.stats.total_edges,
                )
                session.add(row)
            else:
                row.generated_at = breakdown.generated_at
                row.payload_json = breakdown.model_dump_json()
                row.node_count = breakdown.stats.total_nodes
                row.edge_count = breakdown.stats.total_edges
            session.commit()
        logger.info(
            "graph_snapshot_upserted",
            database_name=breakdown.database_name,
            node_count=breakdown.stats.total_nodes,
            edge_count=breakdown.stats.total_edges,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_current(self, database_name: str) -> GraphBreakdown | None:
        """Return the latest snapshot or None if no row exists.

        Args:
            database_name: Database whose snapshot to fetch.

        Returns:
            Deserialised ``GraphBreakdown`` or ``None``.

        """
        with Session(self._engine) as session:
            row = session.get(GraphSnapshot, database_name)
            if row is None:
                return None
            return GraphBreakdown.model_validate_json(row.payload_json)

    def get_staleness_info(self, database_name: str) -> SnapshotStalenessInfo | None:
        """Return lightweight metadata without deserialising the payload.

        Reads only the scalar columns (``generated_at``, ``node_count``,
        ``edge_count``) so callers can decide whether to rebuild without
        the cost of parsing the full JSON payload.

        Args:
            database_name: Database whose staleness info to fetch.

        Returns:
            ``SnapshotStalenessInfo`` or ``None`` if no row exists.

        """
        with Session(self._engine) as session:
            row = session.get(GraphSnapshot, database_name)
            if row is None:
                return None
            # SQLite stores datetimes without tz info; reattach UTC so callers
            # receive an aware datetime consistent with the stored breakdown.
            generated_at = row.generated_at
            if generated_at.tzinfo is None:
                generated_at = generated_at.replace(tzinfo=UTC)
            return SnapshotStalenessInfo(
                generated_at=generated_at,
                node_count=row.node_count,
                edge_count=row.edge_count,
            )
