# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for continue_on_error semantics (finding #11)."""

from typing import Any

import pytest

from chaoscypher_core.services.workflows.engine.executor import (
    create_tool_execution_node,
)
from chaoscypher_core.services.workflows.engine.state import WorkflowState


class AlwaysFailExecutor:
    async def execute_tool(
        self, tool_id: str, inputs: dict[str, Any], thinking_mode: str | None = None
    ) -> dict[str, Any]:
        raise RuntimeError("permanent failure")


@pytest.mark.asyncio
async def test_continue_on_error_true_does_not_poison_state_error() -> None:
    step_def = {
        "id": "s1",
        "tool_type": "system_tool",
        "tool_id": "t",
        "configuration": {},
        "max_retries": 0,
        "continue_on_error": True,
    }
    node = create_tool_execution_node(step_def=step_def, tool_executor=AlwaysFailExecutor())
    state = WorkflowState(workflow_id="w", execution_id="e", database_name="d")
    result = await node(state)
    assert result.error is None
    assert result.status != "failed"
    assert result.step_results["s1"]["error"].startswith("permanent failure")
    assert "s1" in result.step_errors


@pytest.mark.asyncio
async def test_continue_on_error_false_sets_state_error() -> None:
    step_def = {
        "id": "s1",
        "tool_type": "system_tool",
        "tool_id": "t",
        "configuration": {},
        "max_retries": 0,
        "continue_on_error": False,
    }
    node = create_tool_execution_node(step_def=step_def, tool_executor=AlwaysFailExecutor())
    state = WorkflowState(workflow_id="w", execution_id="e", database_name="d")
    result = await node(state)
    assert result.error is not None
    assert result.status == "failed"
