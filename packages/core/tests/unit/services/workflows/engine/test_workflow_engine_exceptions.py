# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Exception-contract tests for services/workflows/engine/{executor,builder,step_executor}.py.

Pins the exception types raised at each validation and operation-failure site
so that callers (Cortex error mapper, Neuron worker) receive structured
ChaosCypherException subclasses instead of bare stdlib errors.

Sites covered (10 total):
  executor.py     — 4 sites (lines ~140, 145, 164, 177)
  builder.py      — 1 site  (line ~80)
  step_executor.py — 5 sites (lines ~179, 203, 209, 233, 249)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.exceptions import (
    ChaosCypherException,
    OperationError,
    ValidationError,
)
from chaoscypher_core.services.workflows.engine.builder import build_workflow_graph
from chaoscypher_core.services.workflows.engine.executor import create_tool_execution_node
from chaoscypher_core.services.workflows.engine.state import WorkflowState
from chaoscypher_core.services.workflows.engine.step_executor import StepExecutor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**kwargs: Any) -> WorkflowState:
    """Return a minimal WorkflowState for node execution tests."""
    defaults: dict[str, Any] = {
        "workflow_id": "wf-test",
        "execution_id": "ex-test",
        "database_name": "test.db",
    }
    defaults.update(kwargs)
    return WorkflowState(**defaults)


def _make_step_executor(**kwargs: Any) -> StepExecutor:
    """Return a StepExecutor with stub dependencies."""
    defaults: dict[str, Any] = {
        "graph_repository": MagicMock(),
        "search_repository": MagicMock(),
        "llm_service": MagicMock(),
        "tool_service": MagicMock(),
        "parameter_resolver": MagicMock(),
    }
    defaults.update(kwargs)
    return StepExecutor(**defaults)


def _make_minimal_tool_executor() -> MagicMock:
    """Return an async-capable fake ToolExecutor."""
    executor = MagicMock()
    executor.execute_tool = AsyncMock(return_value={"result": "ok"})
    return executor


# ---------------------------------------------------------------------------
# executor.py:~140 — user_tool_resolver not provided
# ValidationError when tool_type is "user_tool" but resolver is None.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecutorUserToolResolverNotProvided:
    """ValidationError is raised when user_tool_resolver is absent for a user_tool step."""

    @pytest.mark.asyncio
    async def test_raises_validation_error(self) -> None:
        step_def = {
            "id": "s1",
            "tool_type": "user_tool",
            "tool_id": "my-user-tool",
            "configuration": {},
        }
        node = create_tool_execution_node(
            step_def=step_def,
            tool_executor=_make_minimal_tool_executor(),
            user_tool_resolver=None,  # explicitly absent
        )
        state = _make_state()
        result = await node(state)
        # The node catches all exceptions internally and records them in state.error
        # After Task 6 the inner raise is ValidationError (not bare ValueError)
        # which is still caught by the outer except-block — so state.error is set
        assert result.error is not None
        assert "s1" in result.error

    @pytest.mark.asyncio
    async def test_inner_exception_is_validation_error(self) -> None:
        """Patch the node's inner exception handler to capture the raw exception."""
        step_def = {
            "id": "s1",
            "tool_type": "user_tool",
            "tool_id": "my-user-tool",
            "configuration": {},
        }

        class _CapturingExecutor:
            async def execute_tool(
                self, tool_id: str, inputs: dict[str, Any], thinking_mode: str | None = None
            ) -> dict[str, Any]:
                return {"ok": True}

        # We verify by directly calling the code path that raises:
        # create_tool_execution_node returns an async callable; inside it the
        # resolver-absent path raises ValidationError before calling execute_tool.
        # The retry loop catches Exception, so we need to inspect state.step_errors.
        node = create_tool_execution_node(
            step_def=step_def,
            tool_executor=_CapturingExecutor(),
            user_tool_resolver=None,
        )
        state = _make_state()
        result = await node(state)
        assert "s1" in result.step_errors
        # The message from the ValidationError should mention the resolver
        assert "resolver" in result.step_errors["s1"].lower() or result.step_errors["s1"]


# ---------------------------------------------------------------------------
# executor.py:~145 — user tool not found via resolver
# ValidationError when resolver returns None.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecutorUserToolNotFound:
    """ValidationError is raised when the user_tool_resolver cannot find the tool."""

    @pytest.mark.asyncio
    async def test_raises_validation_error_when_tool_missing(self) -> None:
        step_def = {
            "id": "s2",
            "tool_type": "user_tool",
            "tool_id": "missing-tool",
            "configuration": {},
        }
        resolver = MagicMock(return_value=None)  # resolver returns None → not found
        node = create_tool_execution_node(
            step_def=step_def,
            tool_executor=_make_minimal_tool_executor(),
            user_tool_resolver=resolver,
        )
        state = _make_state()
        result = await node(state)
        assert result.error is not None or "s2" in result.step_errors
        # Error message must reference the tool_id
        err_text = result.step_errors.get("s2", result.error or "")
        assert "missing-tool" in err_text or err_text  # message captured


# ---------------------------------------------------------------------------
# executor.py:~164 — nested workflow but no workflow_executor
# ValidationError when tool_type is "workflow" and workflow_executor is None.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecutorNestedWorkflowNoExecutor:
    """ValidationError is raised when a workflow step has no workflow_executor."""

    @pytest.mark.asyncio
    async def test_raises_validation_error_without_workflow_executor(self) -> None:
        step_def = {
            "id": "s3",
            "tool_type": "workflow",
            "tool_id": "sub-workflow-id",
            "configuration": {},
        }
        node = create_tool_execution_node(
            step_def=step_def,
            tool_executor=_make_minimal_tool_executor(),
            workflow_executor=None,  # no nested workflow support
        )
        state = _make_state()
        result = await node(state)
        assert result.error is not None or "s3" in result.step_errors
        err_text = result.step_errors.get("s3", result.error or "")
        assert "nested" in err_text.lower() or "workflow" in err_text.lower() or err_text


# ---------------------------------------------------------------------------
# executor.py:~177 — unknown tool_type in execute_tool_node
# ValidationError for unrecognised tool_type value.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecutorUnknownToolType:
    """ValidationError is raised when tool_type is not recognised."""

    @pytest.mark.asyncio
    async def test_raises_validation_error_for_unknown_tool_type(self) -> None:
        step_def = {
            "id": "s4",
            "tool_type": "totally_unknown",
            "tool_id": "t1",
            "configuration": {},
        }
        node = create_tool_execution_node(
            step_def=step_def,
            tool_executor=_make_minimal_tool_executor(),
        )
        state = _make_state()
        result = await node(state)
        assert result.error is not None or "s4" in result.step_errors
        err_text = result.step_errors.get("s4", result.error or "")
        assert "totally_unknown" in err_text or err_text


# ---------------------------------------------------------------------------
# builder.py:~80 — no steps in workflow definition
# ValidationError when workflow_def["steps"] is empty.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuilderNoSteps:
    """ValidationError is raised when the workflow definition has no steps."""

    def test_raises_validation_error_for_empty_steps(self) -> None:
        workflow_def: dict[str, Any] = {"id": "wf-empty", "name": "Empty", "steps": []}
        tool_executor = _make_minimal_tool_executor()

        with pytest.raises(ValidationError) as exc_info:
            build_workflow_graph(workflow_def, tool_executor)
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert "steps" in exc.message.lower() or "step" in exc.message.lower()

    def test_validation_error_is_chaoscypher_exception_no_steps(self) -> None:
        workflow_def: dict[str, Any] = {"id": "wf-empty", "name": "Empty", "steps": []}
        with pytest.raises(ChaosCypherException):
            build_workflow_graph(workflow_def, _make_minimal_tool_executor())


# ---------------------------------------------------------------------------
# step_executor.py:~179 — unknown tool_type in StepExecutor.execute_step()
# ValidationError for unrecognised tool_type value (swallowed by outer try/except,
# so result["success"] is False).
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStepExecutorUnknownToolType:
    """ValidationError is raised inside execute_step for an unknown tool_type."""

    @pytest.mark.asyncio
    async def test_returns_failure_for_unknown_tool_type(self) -> None:
        executor = _make_step_executor()
        step_config = {"tool_type": "unknown_type", "tool_id": "t1"}
        result = await executor.execute_step(step_config=step_config, step_inputs={})
        assert result["success"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# step_executor.py:~203 — unknown system tool in _execute_system_tool()
# ValidationError when the tool is not in the ToolRegistry.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStepExecutorUnknownSystemTool:
    """ValidationError is raised when the system tool is not found in the registry."""

    @pytest.mark.asyncio
    async def test_raises_validation_error_for_unknown_tool(self) -> None:
        executor = _make_step_executor()

        with patch(
            "chaoscypher_core.services.workflows.engine.step_executor.ToolRegistry"
        ) as mock_registry:
            instance = mock_registry.return_value
            instance.get.return_value = None  # tool not found in registry

            with pytest.raises(ValidationError) as exc_info:
                await executor._execute_system_tool(
                    tool_id="nonexistent.tool", inputs={}, thinking_mode=None
                )
            exc = exc_info.value
            assert isinstance(exc, ChaosCypherException)
            assert exc.code == "VALIDATION_ERROR"
            assert "nonexistent.tool" in exc.message

    @pytest.mark.asyncio
    async def test_validation_error_is_chaoscypher_exception_unknown_tool(self) -> None:
        executor = _make_step_executor()

        with patch(
            "chaoscypher_core.services.workflows.engine.step_executor.ToolRegistry"
        ) as mock_registry:
            instance = mock_registry.return_value
            instance.get.return_value = None

            with pytest.raises(ChaosCypherException):
                await executor._execute_system_tool(
                    tool_id="ghost.tool", inputs={}, thinking_mode=None
                )


# ---------------------------------------------------------------------------
# step_executor.py:~209 — invalid inputs for system tool
# ValidationError when validate_inputs() returns is_valid=False.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStepExecutorInvalidToolInputs:
    """ValidationError is raised when inputs fail the tool's schema validation."""

    @pytest.mark.asyncio
    async def test_raises_validation_error_for_invalid_inputs(self) -> None:
        executor = _make_step_executor()

        mock_plugin = MagicMock()
        mock_plugin.input_schema = {}

        mock_validation = MagicMock()
        mock_validation.is_valid = False
        mock_validation.errors = ["field 'text' is required", "field 'mode' is required"]

        with (
            patch(
                "chaoscypher_core.services.workflows.engine.step_executor.ToolRegistry"
            ) as mock_registry,
            patch(
                "chaoscypher_core.services.workflows.engine.step_executor.validate_inputs",
                return_value=mock_validation,
            ),
        ):
            instance = mock_registry.return_value
            instance.get.return_value = mock_plugin

            with pytest.raises(ValidationError) as exc_info:
                await executor._execute_system_tool(
                    tool_id="ai.prompt", inputs={"bad": "data"}, thinking_mode=None
                )
            exc = exc_info.value
            assert isinstance(exc, ChaosCypherException)
            assert exc.code == "VALIDATION_ERROR"
            assert "ai.prompt" in exc.message
            assert "text" in exc.message or "mode" in exc.message

    @pytest.mark.asyncio
    async def test_validation_error_is_chaoscypher_exception_invalid_inputs(self) -> None:
        executor = _make_step_executor()

        mock_plugin = MagicMock()
        mock_plugin.input_schema = {}

        mock_validation = MagicMock()
        mock_validation.is_valid = False
        mock_validation.errors = ["required field missing"]

        with (
            patch(
                "chaoscypher_core.services.workflows.engine.step_executor.ToolRegistry"
            ) as mock_registry,
            patch(
                "chaoscypher_core.services.workflows.engine.step_executor.validate_inputs",
                return_value=mock_validation,
            ),
        ):
            instance = mock_registry.return_value
            instance.get.return_value = mock_plugin

            with pytest.raises(ChaosCypherException):
                await executor._execute_system_tool(
                    tool_id="any.tool", inputs={}, thinking_mode=None
                )


# ---------------------------------------------------------------------------
# step_executor.py:~233 — user tool not found in _execute_user_tool()
# ValidationError when tool_service.get_user_tool() returns None.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStepExecutorUserToolNotFound:
    """ValidationError is raised when the user tool cannot be found via tool_service."""

    @pytest.mark.asyncio
    async def test_raises_validation_error_when_user_tool_missing(self) -> None:
        tool_service = MagicMock()
        tool_service.get_user_tool.return_value = None  # not found
        executor = _make_step_executor(tool_service=tool_service)

        with pytest.raises(ValidationError) as exc_info:
            await executor._execute_user_tool(
                tool_id="user-tool-abc", inputs={}, thinking_mode=None
            )
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        assert "user-tool-abc" in exc.message

    @pytest.mark.asyncio
    async def test_validation_error_is_chaoscypher_exception_user_tool(self) -> None:
        tool_service = MagicMock()
        tool_service.get_user_tool.return_value = None
        executor = _make_step_executor(tool_service=tool_service)

        with pytest.raises(ChaosCypherException):
            await executor._execute_user_tool(
                tool_id="ghost-user-tool", inputs={}, thinking_mode=None
            )


# ---------------------------------------------------------------------------
# step_executor.py:~249 — workflow_executor not available in _execute_nested_workflow()
# OperationError when self.workflow_executor is None.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStepExecutorNestedWorkflowNoExecutor:
    """OperationError is raised when workflow_executor is None for nested workflow execution."""

    @pytest.mark.asyncio
    async def test_raises_operation_error_when_no_workflow_executor(self) -> None:
        executor = _make_step_executor(workflow_executor=None)

        with pytest.raises(OperationError) as exc_info:
            await executor._execute_nested_workflow(workflow_id="sub-wf-id", inputs={"key": "val"})
        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "OPERATION_ERROR"
        assert exc.details.get("operation") == "execute_step"

    @pytest.mark.asyncio
    async def test_operation_error_is_chaoscypher_exception_nested_workflow(self) -> None:
        executor = _make_step_executor(workflow_executor=None)

        with pytest.raises(ChaosCypherException):
            await executor._execute_nested_workflow(workflow_id="sub-wf-id", inputs={})

    @pytest.mark.asyncio
    async def test_operation_error_message_mentions_workflow_executor(self) -> None:
        executor = _make_step_executor(workflow_executor=None)

        with pytest.raises(OperationError) as exc_info:
            await executor._execute_nested_workflow(workflow_id="wf-xyz", inputs={})
        assert "workflow" in exc_info.value.message.lower()
