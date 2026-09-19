# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The event-dispatch loop must not run synchronous storage work inline.

``_handle_event`` already offloads ``list_triggers`` and ``get_workflow``
with the comment "sync storage reads must not stall the event-dispatch
loop". Two siblings in the same function were left on the loop:

- ``_should_skip_auto_embed`` -> ``graph_manager.get_node``, on the
  per-entity auto-embed path (one event per created node during import).
- ``event_bus.emit``, which writes a system-event row and then prunes the
  table — two commits whose busy-retry backoff sleeps synchronously.

These tests pin both to a worker thread.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.workflows.triggers.engine.executor import TriggerExecutor


def _make_executor(graph_manager: Any) -> TriggerExecutor:
    # Production wires graph_manager=graph_repository (neuron
    # setup/ops_handlers.py); the auto-embed skip check reads graph_manager.
    return TriggerExecutor(
        trigger_service=MagicMock(),
        workflow_service=MagicMock(),
        tool_service=MagicMock(),
        llm_service=MagicMock(),
        graph_repository=graph_manager,
        search_repository=MagicMock(),
        database_name="test_db",
        execute_workflow_fn=MagicMock(),
        graph_manager=graph_manager,
    )


@pytest.mark.asyncio
async def test_auto_embed_node_lookup_runs_off_the_event_loop() -> None:
    """``get_node`` is a sync storage read and must be offloaded."""
    loop_thread = threading.get_ident()
    seen: list[int] = []

    graph_manager = MagicMock()

    def _get_node(_entity_id: str) -> Any:
        seen.append(threading.get_ident())
        return None

    graph_manager.get_node = _get_node
    executor = _make_executor(graph_manager)

    await executor._should_skip_auto_embed(True, {"entity_type": "node", "entity_id": "node-1"})

    assert seen, "get_node was never called"
    assert seen[0] != loop_thread, "get_node ran on the event-dispatch loop"


@pytest.mark.asyncio
async def test_trigger_fired_event_emit_runs_off_the_event_loop() -> None:
    """``event_bus.emit`` commits twice and must not block the dispatch loop."""
    loop_thread = threading.get_ident()
    seen: list[int] = []

    executor = _make_executor(MagicMock())
    executor.trigger_service.list_triggers = MagicMock(
        return_value=[
            {
                "id": "trig-1",
                "name": "on node create",
                "workflow_id": "workflow-abc",  # not the auto-embed workflow
                "filters": {},
            }
        ]
    )

    def _emit(*_args: Any, **_kwargs: Any) -> None:
        seen.append(threading.get_ident())

    with patch("chaoscypher_core.services.workflows.triggers.engine.executor.event_bus") as bus:
        bus.emit = _emit
        await executor._handle_event({"source": "node.create", "data": {"entity_id": "n-1"}})

    assert seen, "event_bus.emit was never called"
    assert seen[0] != loop_thread, "event_bus.emit ran on the event-dispatch loop"
