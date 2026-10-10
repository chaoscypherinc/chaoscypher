# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""traverse_path over indexed per-node edge lookups matches the legacy
full-edge-table adjacency build on a real SQLite graph.

The legacy reference reproduces the pre-change behaviour: load every edge via
``list_edges_minimal`` and index it into outgoing/incoming adjacency maps.
That query has no ORDER BY, so its row order (and hence which edge first
reaches a node, and which nodes survive ``limit``) was planner-defined; the
per-node lookups order by edge id. The exact comparison therefore runs the
legacy build over id-sorted rows; the raw legacy order is compared on the
order-independent part (which nodes are reached, at which depth).
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode, GraphTemplate, SourceRow
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.services.workflows.tools.engine.handlers.analytics_handlers import (
    AnalyticsToolHandlers,
)


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


class _LegacyFullScanHandlers(AnalyticsToolHandlers):
    """Pre-change neighbour lookup: adjacency built from the whole edge table."""

    sort_by_id = True

    def _incident_edges(
        self, node_id: str, edge_types: list[str] | None
    ) -> tuple[list[Any], list[Any]]:
        outgoing: dict[str, list[Any]] = defaultdict(list)
        incoming: dict[str, list[Any]] = defaultdict(list)
        all_edges = self._load_edges()
        if self.sort_by_id:
            all_edges.sort(key=lambda e: e.id)
        for edge in all_edges:
            if edge_types and edge.label not in edge_types and edge.template_id not in edge_types:
                continue
            outgoing[edge.source_node_id].append(edge)
            incoming[edge.target_node_id].append(edge)
        return outgoing.get(node_id, []), incoming.get(node_id, [])


@pytest.fixture
def graph_repo(integration_adapter: SqliteAdapter) -> GraphRepository:
    """A small seeded graph: enabled + disabled source, cycles, self-loop, parallels."""
    assert integration_adapter.session is not None
    repo = GraphRepository(integration_adapter.session, database_name="default")
    now = datetime.now(UTC)
    for tid, ttype in (("tpl", "node"), ("knows", "edge"), ("likes", "edge")):
        repo.session.add(
            GraphTemplate(
                id=tid,
                database_name="default",
                name=tid,
                template_type=ttype,
                created_at=now,
                updated_at=now,
            )
        )
    for src, enabled in (("src-on", True), ("src-off", False)):
        repo.session.add(
            SourceRow(id=src, database_name="default", filename=src, filepath=src, enabled=enabled)
        )
    repo.session.maybe_commit()
    common = {"database_name": "default", "graph_name": "knowledge"}
    node_src = {f"n{i:02d}": ("src-off" if i >= 16 else "src-on") for i in range(20)}
    for nid, src in node_src.items():
        repo.session.add(
            GraphNode(id=nid, label=nid.upper(), template_id="tpl", source_id=src, **common)
        )
    repo.session.maybe_commit()
    rng = random.Random(1032)
    pairs = [(rng.choice(list(node_src)), rng.choice(list(node_src))) for _ in range(55)]
    pairs += [("n00", "n00"), ("n00", "n01"), ("n00", "n01")]  # self-loop + parallel edges
    for i, (s, t) in enumerate(pairs):
        tpl = rng.choice(["knows", "likes"])
        repo.session.add(
            GraphEdge(
                id=f"e{i:03d}",
                source_node_id=s,
                target_node_id=t,
                label=f"{tpl}_rel",
                template_id=tpl,
                source_id=node_src[s],
                **common,
            )
        )
        repo.session.maybe_commit()  # one row per commit keeps rowid order == id order
    repo.session.expire_all()
    return repo


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "edge_types", "max_depth", "limit"),
    [
        ("n00", None, 2, 50),
        ("n00", None, 4, 50),
        ("n03", None, 3, 5),
        ("n05", ["knows"], 3, 50),
        ("n07", ["likes_rel"], 2, 50),
        ("n17", None, 2, 50),
        ("n00", None, 1, 1),
    ],
)
async def test_indexed_traversal_matches_legacy_full_scan(
    graph_repo: GraphRepository,
    start: str,
    edge_types: list[str] | None,
    max_depth: int,
    limit: int,
) -> None:
    """New per-node traversal returns exactly what the full-table version did."""
    kwargs: dict[str, Any] = {
        "graph_repository": graph_repo,
        "search_repository": MagicMock(),
        "analytics_service": MagicMock(),
    }
    legacy = await _LegacyFullScanHandlers(**kwargs).traverse_path(
        start, edge_types=edge_types, max_depth=max_depth, limit=limit
    )
    indexed = await AnalyticsToolHandlers(**kwargs).traverse_path(
        start, edge_types=edge_types, max_depth=max_depth, limit=limit
    )

    assert legacy["success"] is True
    assert indexed == legacy

    if limit == 50:  # limit not binding: reach set + depths are order-independent
        assert legacy["nodes_found"] > 0  # the case actually exercises traversal
        raw_handler = _LegacyFullScanHandlers(**kwargs)
        raw_handler.sort_by_id = False
        raw = await raw_handler.traverse_path(
            start, edge_types=edge_types, max_depth=max_depth, limit=limit
        )

        def depths(res: dict[str, Any]) -> dict[str, int]:
            return {r["node"]["id"]: r["depth"] for r in res["results"]}

        assert depths(indexed) == depths(raw)
