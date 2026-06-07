# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Executor Node - LangGraph Integration.

Creates tool execution nodes for LangGraph workflows.
Supports system tools, user tools, and nested workflows.

Works with any tool executor (backend or CLI implementation).
"""

import asyncio
from collections.abc import Callable
from typing import Any, Protocol, cast

import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.workflows.engine.interpolator import ParameterInterpolator
from chaoscypher_core.services.workflows.engine.state import (
    WorkflowState,  # noqa: TC001 — must be runtime-importable; langgraph 1.1.9 calls inspect.signature(eval_str=True) when registering nodes.
)
from chaoscypher_core.utils.retry import _backoff_delay


logger = structlog.get_logger(__name__)


# Protocol for tool executors (backend and CLI can implement differently)
class ToolExecutor(Protocol):
    """Protocol for executing tools in workflows."""

    async def execute_tool(
        self, tool_id: str, inputs: dict[str, Any], thinking_mode: str | None = None
    ) -> dict[str, Any]:
        """Execute a tool and return results."""
        ...


class WorkflowExecutor(Protocol):
    """Protocol for executing nested workflows."""

    async def execute_workflow(
        self,
        workflow_id: str,
        inputs: dict[str, Any],
        triggered_by: str = "workflow",
        parent_execution_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a workflow and return results."""
        ...


async def _execute_step_tool(
    *,
    tool_type: str | None,
    tool_id: str | None,
    thinking_mode: str | None,
    interpolated_params: dict[str, Any],
    tool_executor: ToolExecutor,
    workflow_executor: WorkflowExecutor | None,
    user_tool_resolver: Callable | None,
    parent_execution_id: str,
) -> dict[str, Any]:
    """Execute a step's tool by type and return its raw output dict.

    Extracted from the node closure so it can be wrapped in a single
    ``asyncio.wait_for`` and to keep the node within complexity limits.

    Raises:
        ValidationError: unknown tool type, missing user-tool resolver, the
            resolved user tool is not found, or a nested workflow is requested
            without a workflow_executor.
    """
    if tool_type in ("system_tool", "SYSTEM_TOOL"):
        # Execute system tool directly via executor
        assert tool_id is not None, "tool_id must be set for system_tool"
        return await tool_executor.execute_tool(
            tool_id=tool_id,
            inputs=interpolated_params,
            thinking_mode=thinking_mode,
        )

    if tool_type in ("user_tool", "USER_TOOL"):
        # Resolve user tool configuration
        if not user_tool_resolver:
            msg = "User tool resolver not provided"
            raise ValidationError(msg)

        user_tool = user_tool_resolver(tool_id)
        if not user_tool:
            msg = f"User tool not found: {tool_id}"
            raise ValidationError(msg)

        # Merge user tool config with step parameters, then execute the
        # underlying system tool.
        merged_params = {
            **user_tool.get("configuration", {}),
            **interpolated_params,
        }
        return await tool_executor.execute_tool(
            tool_id=user_tool["system_tool_id"],
            inputs=merged_params,
            thinking_mode=thinking_mode,
        )

    if tool_type in ("workflow", "WORKFLOW"):
        # Execute nested workflow
        if not workflow_executor:
            msg = "Nested workflows not supported (workflow_executor not provided)"
            raise ValidationError(msg)

        assert tool_id is not None, "tool_id must be set for workflow"
        result = await workflow_executor.execute_workflow(
            workflow_id=tool_id,
            inputs=interpolated_params,
            triggered_by="workflow",
            parent_execution_id=parent_execution_id,
        )
        return cast("dict[str, Any]", result.get("outputs", {}))

    msg = f"Unknown tool type: {tool_type}"
    raise ValidationError(msg)


def create_tool_execution_node(
    step_def: dict[str, Any],
    tool_executor: ToolExecutor,
    workflow_executor: WorkflowExecutor | None = None,
    user_tool_resolver: Callable | None = None,
) -> Callable:
    """Create a tool execution node for LangGraph.

    This function creates nodes that work in both backend and CLI modes.
    Dependencies are injected via Protocols.

    Args:
        step_def: Step definition from workflow containing:
            - id: Step identifier
            - tool_type: Type of tool (system_tool, user_tool, workflow)
            - tool_id: Tool to execute
            - configuration: Tool parameters
            - thinking_mode: Optional AI thinking mode
        tool_executor: ToolExecutor implementation (backend or CLI)
        workflow_executor: Optional WorkflowExecutor for nested workflows
        user_tool_resolver: Optional function to resolve user tools

    Returns:
        Async function that transforms WorkflowState

    Raises:
        ValidationError: If the step tool type is unknown, the user_tool_resolver
            is not provided for a user_tool step, the resolved user tool is not
            found, or the workflow_executor is not provided for a nested workflow step.

    Example (Backend):
        >>> from backend.features.tools.ai_executor import BackendToolExecutor
        >>> executor = BackendToolExecutor(graph_repo, llm_service, ...)
        >>> node = create_tool_execution_node(step_def, executor)

    Example (CLI):
        >>> from cli.executors import CLIToolExecutor
        >>> executor = CLIToolExecutor(graph_repo)
        >>> node = create_tool_execution_node(step_def, executor)

    """
    step_id = step_def["id"]
    tool_type = step_def.get("tool_type")
    tool_id = step_def.get("tool_id")
    configuration = step_def.get("configuration", {})
    thinking_mode = step_def.get("thinking_mode")
    max_retries = int(step_def.get("max_retries", 0))
    continue_on_error = bool(step_def.get("continue_on_error", False))
    timeout_seconds = step_def.get("timeout_seconds")

    async def execute_tool_node(state: WorkflowState) -> WorkflowState:
        """Execute tool and update state.

        LangGraph calls this function with current state.
        Returns updated state.
        """
        # Fail-stop poison-pill. In a real DAG, the builder's static AND-join
        # edges still route to downstream steps after a sibling branch has
        # failed. If a prior step already poisoned the shared state, skip this
        # step's tool so a join never runs with missing/partial upstream
        # inputs. This sits above the retry/timeout logic and does not alter it;
        # a soft (continue_on_error) failure never sets state.error, so it does
        # not trip this guard.
        if state.error is not None:
            logger.info(
                "workflow_step_skipped_prior_error",
                step_id=step_id,
                prior_error=state.error,
            )
            return state

        logger.info(
            "workflow_step_executing",
            step_id=step_id,
            step_name=step_def.get("name", tool_id),
            tool_type=tool_type,
            max_retries=max_retries,
        )

        attempt = 0
        last_exc: Exception | None = None
        app_settings = get_settings()
        workflows_settings = app_settings.workflows
        backoff_multiplier = app_settings.backoff.exponential_multiplier

        async def _sleep_backoff() -> None:
            """Wait the capped exponential backoff before the next attempt.

            Reads ``attempt`` from the enclosing scope after it has been
            incremented, so the first retry waits ``base_delay`` (exponent 0).
            """
            await asyncio.sleep(
                _backoff_delay(
                    attempt - 1,
                    workflows_settings.step_retry_base_delay_seconds,
                    workflows_settings.step_retry_max_delay_seconds,
                    exponential_multiplier=backoff_multiplier,
                )
            )

        while attempt <= max_retries:
            try:
                # Build execution context for interpolation
                context = {
                    "inputs": state.initial_inputs,
                    "steps": state.step_results,
                    "workflow_id": state.workflow_id,
                    "execution_id": state.execution_id,
                }

                # Interpolate parameters using engine's interpolator. Kept
                # outside the timeout wrapper below: interpolation is cheap and
                # synchronous, and an interpolation error should route through
                # the retry / continue_on_error path, not be reported as a
                # tool timeout.
                interpolated_params = ParameterInterpolator.interpolate_parameters(
                    configuration, context
                )

                step_coro = _execute_step_tool(
                    tool_type=tool_type,
                    tool_id=tool_id,
                    thinking_mode=thinking_mode,
                    interpolated_params=interpolated_params,
                    tool_executor=tool_executor,
                    workflow_executor=workflow_executor,
                    user_tool_resolver=user_tool_resolver,
                    parent_execution_id=state.execution_id,
                )

                # Enforce the persisted per-step timeout. A hung tool/LLM call
                # otherwise holds a worker slot forever; wait_for cancels the
                # dispatch coroutine and raises TimeoutError once the budget is
                # exceeded. None / 0 / negative all mean "no cap".
                if timeout_seconds is not None and timeout_seconds > 0:
                    output = await asyncio.wait_for(step_coro, timeout=float(timeout_seconds))
                else:
                    output = await step_coro

                # Update state
                state.step_results[step_id] = output
                state.current_step = step_id
                state.status = "running"

                logger.info(
                    "workflow_step_completed",
                    step_id=step_id,
                    has_output=bool(output),
                    attempts=attempt + 1,
                )
                return state

            except TimeoutError as e:
                # asyncio.wait_for raises a message-less TimeoutError when the
                # step's own budget is exceeded; synthesize a clear message for
                # that case. A TimeoutError raised by the tool itself carries a
                # message — preserve it verbatim rather than mislabeling it as a
                # budget overrun.
                last_exc = (
                    TimeoutError(f"Step {step_id} timed out after {timeout_seconds}s")
                    if timeout_seconds and not str(e)
                    else e
                )
                attempt += 1
                logger.warning(
                    "workflow_step_timeout",
                    step_id=step_id,
                    attempt=attempt,
                    max_retries=max_retries,
                    timeout_seconds=timeout_seconds,
                    error_message=str(last_exc),
                )
                if attempt > max_retries:
                    break
                await _sleep_backoff()

            except Exception as e:
                last_exc = e
                attempt += 1
                logger.warning(
                    "workflow_step_attempt_failed",
                    step_id=step_id,
                    attempt=attempt,
                    max_retries=max_retries,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                if attempt > max_retries:
                    break
                await _sleep_backoff()

        logger.exception(
            "workflow_step_execution_error",
            step_id=step_id,
            error_type=type(last_exc).__name__ if last_exc else "Unknown",
            error_message=str(last_exc) if last_exc else "",
            attempts=attempt,
        )

        # Record error
        state.step_errors[step_id] = str(last_exc) if last_exc else "unknown error"
        if continue_on_error:
            # Soft failure: record error on the step's output, do NOT poison state.error
            state.step_results[step_id] = {"error": str(last_exc) if last_exc else "unknown error"}
            state.current_step = step_id
            return state
        state.error = f"Step {step_id} failed: {last_exc!s}"
        state.status = "failed"

        return state

    return execute_tool_node


def create_error_handler_node() -> Callable:
    """Create error handler node for workflows.

    Returns:
        Function that handles workflow errors

    """

    async def handle_error(state: WorkflowState) -> WorkflowState:
        """Mark workflow as failed."""
        logger.exception(
            "workflow_error_handler",
            workflow_id=state.workflow_id,
            execution_id=state.execution_id,
            error=state.error,
            failed_step=state.current_step,
        )
        state.status = "failed"
        return state

    return handle_error


__all__ = [
    "ToolExecutor",
    "WorkflowExecutor",
    "create_error_handler_node",
    "create_tool_execution_node",
]
