# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the quality-score startup recalculation check.

Drives ``queue_outdated_quality_score_recalculation`` with a MagicMock SQLite
adapter and queue client (both lazily imported, so patched at their source
modules). Verifies that only extraction-complete sources with a stale
``cached_scores_version`` are enqueued, the up-to-date no-op path, and the
queue-unavailable log-and-skip branch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.quality.startup import (
    queue_outdated_quality_score_recalculation,
)


# A version constant we patch into the core module so source rows are
# deterministically stale / fresh relative to it.
_VERSION = 7


def _settings() -> MagicMock:
    """Return a settings stub with the fields the startup fn reads."""
    settings = MagicMock()
    settings.current_database = "default"
    settings.priorities.background = 5
    return settings


def _patches(adapter: MagicMock, queue: MagicMock) -> tuple:
    """Return the trio of source-module patches the startup fn lazily imports."""
    return (
        patch(
            "chaoscypher_core.database.get_sqlite_adapter",
            return_value=adapter,
        ),
        patch("chaoscypher_core.queue.queue_client", queue),
        patch("chaoscypher_core.services.quality.SCORING_VERSION", _VERSION),
    )


@pytest.mark.unit
class TestQueueOutdatedRecalculation:
    """Tests for queue_outdated_quality_score_recalculation."""

    @pytest.mark.asyncio
    async def test_enqueues_only_stale_complete_sources(self) -> None:
        """Only complete sources with a version mismatch are enqueued."""
        adapter = MagicMock()
        adapter.list_files.return_value = [
            # Stale version → enqueued.
            {"id": "stale", "extraction_complete": True, "cached_scores_version": 1},
            # Missing version → enqueued.
            {"id": "missing", "extraction_complete": True, "cached_scores_version": None},
            # Up to date → skipped.
            {"id": "fresh", "extraction_complete": True, "cached_scores_version": _VERSION},
            # Incomplete extraction → skipped regardless of version.
            {"id": "incomplete", "extraction_complete": False, "cached_scores_version": 1},
        ]
        queue = MagicMock()
        queue.is_available = True
        queue.enqueue_task = AsyncMock()
        settings = _settings()

        p1, p2, p3 = _patches(adapter, queue)
        with p1, p2, p3:
            await queue_outdated_quality_score_recalculation(settings)

        queue.enqueue_task.assert_awaited_once()
        call_kwargs = queue.enqueue_task.await_args.kwargs
        assert call_kwargs["operation"] == "recalculate_quality_scores"
        assert sorted(call_kwargs["data"]["source_ids"]) == ["missing", "stale"]
        assert call_kwargs["data"]["database_name"] == "default"
        assert call_kwargs["priority"] == 5

    @pytest.mark.asyncio
    async def test_warms_every_stale_source_not_just_the_newest_hundred(self) -> None:
        """``list_files`` defaults to 100 rows; the cache warmer needs them all.

        Anything this pass misses stays stale until a request recalculates it
        inline, so a truncated warm-up shifts that cost onto the first
        ``/quality/summary`` or ``/quality/analyze`` call.
        """
        rows = [
            {"id": f"s{i:03d}", "extraction_complete": True, "cached_scores_version": 1}
            for i in range(150)
        ]
        adapter = MagicMock()
        adapter.list_files.side_effect = lambda database_name, status=None, limit=100: rows[:limit]
        queue = MagicMock()
        queue.is_available = True
        queue.enqueue_task = AsyncMock()

        p1, p2, p3 = _patches(adapter, queue)
        with p1, p2, p3:
            await queue_outdated_quality_score_recalculation(_settings())

        queued = queue.enqueue_task.await_args.kwargs["data"]["source_ids"]
        assert len(queued) == 150

    @pytest.mark.asyncio
    async def test_chunks_large_batches_across_multiple_tasks(self) -> None:
        """Stale sources are split into tasks the handler will not truncate.

        The neuron handler silently drops everything past
        ``max_quality_score_batch`` (default 500) in a single request, and a
        one-shot payload of every source ID would also make the
        ``queue:task:{id}`` hash's ``data`` field multi-megabyte.
        """
        rows = [
            {"id": f"s{i:04d}", "extraction_complete": True, "cached_scores_version": 1}
            for i in range(1250)
        ]
        adapter = MagicMock()
        adapter.list_files.side_effect = lambda database_name, status=None, limit=100: rows[:limit]
        queue = MagicMock()
        queue.is_available = True
        queue.enqueue_task = AsyncMock()

        p1, p2, p3 = _patches(adapter, queue)
        with p1, p2, p3:
            await queue_outdated_quality_score_recalculation(_settings())

        batches = [call.kwargs["data"]["source_ids"] for call in queue.enqueue_task.await_args_list]
        assert len(batches) == 3, "1250 stale sources must fan out across several tasks"
        assert all(len(batch) <= 500 for batch in batches)
        # Every stale source is covered exactly once, in order.
        assert [sid for batch in batches for sid in batch] == [row["id"] for row in rows]

    @pytest.mark.asyncio
    async def test_single_batch_still_enqueues_one_task(self) -> None:
        """A workspace under the chunk size keeps the original single-task shape."""
        rows = [
            {"id": f"s{i}", "extraction_complete": True, "cached_scores_version": 1}
            for i in range(10)
        ]
        adapter = MagicMock()
        adapter.list_files.side_effect = lambda database_name, status=None, limit=100: rows[:limit]
        queue = MagicMock()
        queue.is_available = True
        queue.enqueue_task = AsyncMock()

        p1, p2, p3 = _patches(adapter, queue)
        with p1, p2, p3:
            await queue_outdated_quality_score_recalculation(_settings())

        queue.enqueue_task.assert_awaited_once()
        assert len(queue.enqueue_task.await_args.kwargs["data"]["source_ids"]) == 10

    @pytest.mark.asyncio
    async def test_no_op_when_all_up_to_date(self) -> None:
        """No enqueue happens when every source is already current."""
        adapter = MagicMock()
        adapter.list_files.return_value = [
            {"id": "fresh", "extraction_complete": True, "cached_scores_version": _VERSION},
        ]
        queue = MagicMock()
        queue.is_available = True
        queue.enqueue_task = AsyncMock()

        p1, p2, p3 = _patches(adapter, queue)
        with p1, p2, p3:
            await queue_outdated_quality_score_recalculation(_settings())

        queue.enqueue_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_queue_unavailable(self) -> None:
        """Stale sources are detected but not enqueued when queue is down."""
        adapter = MagicMock()
        adapter.list_files.return_value = [
            {"id": "stale", "extraction_complete": True, "cached_scores_version": 1},
        ]
        queue = MagicMock()
        queue.is_available = False
        queue.enqueue_task = AsyncMock()

        p1, p2, p3 = _patches(adapter, queue)
        with p1, p2, p3:
            await queue_outdated_quality_score_recalculation(_settings())

        queue.enqueue_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_source_list_is_no_op(self) -> None:
        """An empty source list short-circuits without touching the queue."""
        adapter = MagicMock()
        adapter.list_files.return_value = []
        queue = MagicMock()
        queue.is_available = True
        queue.enqueue_task = AsyncMock()

        p1, p2, p3 = _patches(adapter, queue)
        with p1, p2, p3:
            await queue_outdated_quality_score_recalculation(_settings())

        queue.enqueue_task.assert_not_awaited()
