# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for TriggerExecutor input_schema validation of merged inputs."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.services.workflows.triggers.engine.executor import TriggerExecutor


def _executor(workflow_service: MagicMock, execute_fn: AsyncMock) -> TriggerExecutor:
    return TriggerExecutor(
        trigger_service=MagicMock(),
        workflow_service=workflow_service,
        tool_service=MagicMock(),
        llm_service=MagicMock(),
        graph_repository=MagicMock(),
        search_repository=MagicMock(),
        database_name="test_db",
        execute_workflow_fn=execute_fn,
    )


@pytest.mark.asyncio
async def test_invalid_merged_inputs_skip_dispatch_and_record_failure() -> None:
    """When merged_inputs violate workflow.input_schema, executor must NOT call
    execute_workflow_fn, must record a failed stats row, and must not raise.
    """
    workflow_service = MagicMock()
    workflow_service.get_workflow.return_value = {
        "id": "wf-1",
        "name": "Needs integer foo",
        "input_schema": {
            "type": "object",
            "properties": {"foo": {"type": "integer"}},
            "required": ["foo"],
        },
    }
    execute_fn = AsyncMock()

    executor = _executor(workflow_service, execute_fn)

    trigger = {
        "id": "t-1",
        "name": "Bad trigger",
        "workflow_id": "wf-1",
        "workflow_inputs": {"foo": "not-an-integer"},
    }

    await executor._dispatch_trigger_workflow(
        trigger=trigger,
        event_data={"foo": 42},  # valid event data
        event_source="node.created",
        is_auto_embed=False,
    )

    # execute_workflow_fn must NOT be called — inputs invalid
    execute_fn.assert_not_called()
    # A failure stats row must be recorded
    assert executor.stats_tracker.trigger_stats["t-1"].failed >= 1


@pytest.mark.asyncio
async def test_valid_merged_inputs_proceed_to_dispatch() -> None:
    workflow_service = MagicMock()
    workflow_service.get_workflow.return_value = {
        "id": "wf-1",
        "name": "OK",
        "input_schema": {
            "type": "object",
            "properties": {"foo": {"type": "integer"}},
        },
    }
    execute_fn = AsyncMock(return_value={"execution_id": "exec-1"})

    executor = _executor(workflow_service, execute_fn)

    trigger = {
        "id": "t-2",
        "name": "Good trigger",
        "workflow_id": "wf-1",
        "workflow_inputs": None,
    }

    await executor._dispatch_trigger_workflow(
        trigger=trigger,
        event_data={"foo": 42},
        event_source="node.created",
        is_auto_embed=False,
    )

    execute_fn.assert_called_once()


@pytest.mark.asyncio
async def test_missing_input_schema_passes_through() -> None:
    """Workflows without input_schema behave as before (no validation gate)."""
    workflow_service = MagicMock()
    workflow_service.get_workflow.return_value = {"id": "wf-1", "name": "No schema"}
    execute_fn = AsyncMock(return_value={"execution_id": "e"})

    executor = _executor(workflow_service, execute_fn)

    await executor._dispatch_trigger_workflow(
        trigger={"id": "t-3", "name": "T", "workflow_id": "wf-1", "workflow_inputs": {}},
        event_data={"x": 1},
        event_source="src",
        is_auto_embed=False,
    )
    execute_fn.assert_called_once()
