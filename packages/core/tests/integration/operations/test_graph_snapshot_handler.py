# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for handle_build_graph_snapshot operation handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos import GraphSnapshotRepository

from ...fixtures.seed_graph import seed_two_sources_three_templates


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


@pytest.mark.asyncio
async def test_handler_builds_and_stores(integration_adapter: SqliteAdapter) -> None:
    """Whole-DB call persists the snapshot and returns success=True."""
    seed_two_sources_three_templates(integration_adapter)

    from chaoscypher_core.operations.graph_snapshot_handler import (
        handle_build_graph_snapshot,
    )

    result = await handle_build_graph_snapshot(
        data={"database_name": "default"},
        adapter=integration_adapter,
    )

    assert result["success"] is True
    breakdown_dict = result["breakdown"]
    assert breakdown_dict["stats"]["total_nodes"] == 14
    assert breakdown_dict["stats"]["total_sources"] == 2

    engine = get_engine(integration_adapter.db_path)
    repo = GraphSnapshotRepository(engine)
    persisted = repo.get_current("default")
    assert persisted is not None
    assert persisted.stats.total_nodes == breakdown_dict["stats"]["total_nodes"]
    assert persisted.stats.total_edges == breakdown_dict["stats"]["total_edges"]
    assert persisted.stats.total_sources == breakdown_dict["stats"]["total_sources"]
    assert len(persisted.sources) == 2


@pytest.mark.asyncio
async def test_handler_with_source_filter_does_not_persist(
    integration_adapter: SqliteAdapter,
) -> None:
    """Source-filtered call does not overwrite the whole-DB snapshot."""
    seed_two_sources_three_templates(integration_adapter)

    from chaoscypher_core.operations.graph_snapshot_handler import (
        handle_build_graph_snapshot,
    )

    # First: whole-DB call to establish baseline snapshot
    first_result = await handle_build_graph_snapshot(
        data={"database_name": "default"},
        adapter=integration_adapter,
    )
    assert first_result["success"] is True

    engine = get_engine(integration_adapter.db_path)
    repo = GraphSnapshotRepository(engine)
    first_snapshot = repo.get_current("default")
    assert first_snapshot is not None
    first_generated_at = first_snapshot.generated_at

    # Second: source-filtered call — must NOT overwrite persisted snapshot
    filtered_result = await handle_build_graph_snapshot(
        data={"database_name": "default", "source_ids": ["src_a"]},
        adapter=integration_adapter,
    )
    assert filtered_result["success"] is True

    # Returned breakdown scoped to src_a only (8 nodes)
    filtered_breakdown = filtered_result["breakdown"]
    assert filtered_breakdown["stats"]["total_nodes"] == 8
    assert filtered_breakdown["stats"]["total_sources"] == 1

    # Persisted snapshot is still the first one (generated_at unchanged)
    current_snapshot = repo.get_current("default")
    assert current_snapshot is not None
    assert current_snapshot.generated_at == first_generated_at


@pytest.mark.asyncio
async def test_handler_propagates_title(integration_adapter: SqliteAdapter) -> None:
    """Title parameter passes through to the returned breakdown."""
    seed_two_sources_three_templates(integration_adapter)

    from chaoscypher_core.operations.graph_snapshot_handler import (
        handle_build_graph_snapshot,
    )

    result = await handle_build_graph_snapshot(
        data={"database_name": "default", "title": "My Export"},
        adapter=integration_adapter,
    )

    assert result["success"] is True
    assert result["breakdown"]["title"] == "My Export"
