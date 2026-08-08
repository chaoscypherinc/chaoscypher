# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: StepExecutor must accept the orchestrator's calling shape.

``execute_step_task`` in ``operations/workflows/orchestrator.py`` constructs
``StepExecutor`` without a ``parameter_resolver`` and passes
``workflow_executor`` to ``execute_step``. Both signatures had drifted (masked
by ``type: ignore[call-arg]``), so every queued step operation died with
``TypeError`` before reaching the tool. These tests pin the contract without
patching the call site.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.services.workflows.engine.interpolator import ParameterInterpolator
from chaoscypher_core.services.workflows.engine.step_executor import StepExecutor


def _orchestrator_shaped_executor() -> StepExecutor:
    """Construct StepExecutor exactly as execute_step_task does."""
    return StepExecutor(
        graph_repository=MagicMock(),
        search_repository=MagicMock(),
        llm_service=MagicMock(),
        tool_service=MagicMock(),
    )


def test_constructs_without_parameter_resolver() -> None:
    executor = _orchestrator_shaped_executor()
    assert isinstance(executor.parameter_resolver, ParameterInterpolator)


@pytest.mark.asyncio
async def test_execute_step_accepts_workflow_executor_kwarg() -> None:
    executor = _orchestrator_shaped_executor()

    workflow_executor = MagicMock()
    workflow_executor.execute_workflow = AsyncMock(return_value={"outputs": {"answer": 42}})

    result = await executor.execute_step(
        step_config={"tool_type": "workflow", "tool_id": "wf1"},
        step_inputs={},
        workflow_context=None,
        workflow_executor=workflow_executor,
    )

    assert result["success"] is True
    assert result["output"] == {"answer": 42}
    workflow_executor.execute_workflow.assert_awaited_once_with(
        workflow_id="wf1", inputs={}, triggered_by="workflow"
    )
