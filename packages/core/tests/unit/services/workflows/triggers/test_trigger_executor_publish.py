# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for TriggerExecutor.publish_event_sync backpressure semantics."""

import asyncio
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.workflows.triggers.engine.executor import (
    EventPublishResult,
    TriggerExecutor,
)


def _bare_executor() -> TriggerExecutor:
    return TriggerExecutor(
        trigger_service=MagicMock(),
        workflow_service=MagicMock(),
        tool_service=MagicMock(),
        llm_service=MagicMock(),
        graph_repository=MagicMock(),
        search_repository=MagicMock(),
        database_name="test_db",
        execute_workflow_fn=MagicMock(),
    )


def _executor(maxsize: int = 0) -> TriggerExecutor:
    ex = TriggerExecutor(
        trigger_service=MagicMock(),
        workflow_service=MagicMock(),
        tool_service=MagicMock(),
        llm_service=MagicMock(),
        graph_repository=MagicMock(),
        search_repository=MagicMock(),
        database_name="test_db",
        execute_workflow_fn=MagicMock(),
    )
    # Replace queue with a tiny one we can fill
    ex.event_queue = asyncio.Queue(maxsize=maxsize)
    return ex


def test_publish_success_returns_published_true() -> None:
    ex = _executor(maxsize=5)
    result = ex.publish_event_sync("node.created", {"id": "n1"})
    assert isinstance(result, EventPublishResult)
    assert result.published is True
    assert result.dropped is False
    assert result.reason is None


def test_publish_queue_full_returns_dropped_result_and_counts() -> None:
    ex = _executor(maxsize=1)
    ex.event_queue.put_nowait({"filler": True})
    result = ex.publish_event_sync("node.created", {"id": "n2"})
    assert result.published is False
    assert result.dropped is True
    assert result.reason == "queue_full"
    assert ex.events_dropped_total == 1


def test_publish_multiple_drops_accumulate_counter() -> None:
    ex = _executor(maxsize=1)
    ex.event_queue.put_nowait({"filler": True})
    ex.publish_event_sync("x", {})
    ex.publish_event_sync("x", {})
    assert ex.events_dropped_total == 2


def test_default_event_queue_is_bounded() -> None:
    """A freshly constructed executor has a bounded queue, so a stalled consumer
    triggers backpressure (drops) instead of unbounded memory growth.
    """
    ex = _bare_executor()
    assert ex.event_queue.maxsize > 0


@pytest.mark.asyncio
async def test_stop_cancels_running_event_loop_task() -> None:
    """start() launches the event-processing task; stop() cancels and clears it."""
    ex = _bare_executor()
    await ex.start()
    assert ex.is_running is True
    assert ex._process_task is not None

    await ex.stop()
    assert ex.is_running is False
    assert ex._process_task is None
