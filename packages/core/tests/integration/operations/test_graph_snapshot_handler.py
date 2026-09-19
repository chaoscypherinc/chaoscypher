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


@pytest.mark.asyncio
async def test_handler_does_not_pin_the_event_loop(
    integration_adapter: SqliteAdapter,
) -> None:
    """The build runs off the loop, so sibling coroutines keep making progress.

    Regression test for the hunt-queue finding that the handler ran six
    whole-database aggregates plus the persist synchronously on the Neuron
    worker's event loop, stalling every ops slot, the pollers, the heartbeat
    refresher and the reconciler on each post-commit refresh.
    """
    import asyncio
    import time

    seed_two_sources_three_templates(integration_adapter)

    from chaoscypher_core.operations.graph_snapshot_handler import (
        handle_build_graph_snapshot,
    )
    from chaoscypher_core.services.graph.snapshot import build_service as bs_module

    # Make the aggregate phase take measurable wall-clock time, so the
    # assertion measures the offload rather than the size of the seed data.
    real_build = bs_module.BuildGraphSnapshotService.build

    def _slow_build(self: object, *args: object, **kwargs: object) -> object:
        time.sleep(0.2)
        return real_build(self, *args, **kwargs)  # type: ignore[arg-type]

    bs_module.BuildGraphSnapshotService.build = _slow_build  # type: ignore[method-assign]

    ticks = 0

    async def _ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    ticker = asyncio.create_task(_ticker())
    try:
        result = await handle_build_graph_snapshot(
            data={"database_name": "default"},
            adapter=integration_adapter,
        )
    finally:
        ticker.cancel()
        bs_module.BuildGraphSnapshotService.build = real_build  # type: ignore[method-assign]

    assert result["success"] is True
    # A synchronous build yields to the loop exactly never, so the ticker
    # would be frozen for the whole 200 ms; the offloaded one lets it run.
    assert ticks > 0


@pytest.mark.asyncio
async def test_handler_never_touches_the_shared_adapter_session(
    integration_adapter: SqliteAdapter,
) -> None:
    """The offloaded build must use a private session, not the worker's.

    ``adapter.session`` is process-wide and shared with every sibling
    handler, and ``SafeSession`` is not thread-safe — offloading it would
    trade an event-loop stall for cross-thread session corruption.
    """
    seed_two_sources_three_templates(integration_adapter)

    from chaoscypher_core.adapters.sqlite.repos import graph_breakdown as gb_module
    from chaoscypher_core.operations.graph_snapshot_handler import (
        handle_build_graph_snapshot,
    )

    shared_session = integration_adapter.session
    seen_sessions: list[object] = []
    real_init = gb_module.GraphBreakdownQueryRepository.__init__

    def _spy_init(self: object, session: object) -> None:
        seen_sessions.append(session)
        real_init(self, session)  # type: ignore[arg-type]

    gb_module.GraphBreakdownQueryRepository.__init__ = _spy_init  # type: ignore[method-assign]
    try:
        result = await handle_build_graph_snapshot(
            data={"database_name": "default"},
            adapter=integration_adapter,
        )
    finally:
        gb_module.GraphBreakdownQueryRepository.__init__ = real_init  # type: ignore[method-assign]

    assert result["success"] is True
    assert seen_sessions, "the build must construct a query repository"
    assert all(s is not shared_session for s in seen_sessions)
