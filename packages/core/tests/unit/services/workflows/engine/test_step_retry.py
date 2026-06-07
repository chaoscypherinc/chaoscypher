# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for workflow step retry, retry backoff, and timeout enforcement."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from chaoscypher_core.services.workflows.engine.executor import (
    create_tool_execution_node,
)
from chaoscypher_core.services.workflows.engine.state import WorkflowState


class FlakyExecutor:
    """Tool executor that fails N times then succeeds."""

    def __init__(self, fail_count: int) -> None:
        self.fail_count = fail_count
        self.calls = 0

    async def execute_tool(
        self, tool_id: str, inputs: dict[str, Any], thinking_mode: str | None = None
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.fail_count:
            raise RuntimeError(f"transient failure {self.calls}")
        return {"ok": True}


class HangingExecutor:
    """Tool executor whose call blocks far longer than any step timeout."""

    def __init__(self, delay: float = 0.5) -> None:
        self.delay = delay
        self.calls = 0

    async def execute_tool(
        self, tool_id: str, inputs: dict[str, Any], thinking_mode: str | None = None
    ) -> dict[str, Any]:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return {"ok": True}


class SelfTimeoutExecutor:
    """Tool executor that immediately raises its OWN TimeoutError."""

    def __init__(self, message: str) -> None:
        self.message = message
        self.calls = 0

    async def execute_tool(
        self, tool_id: str, inputs: dict[str, Any], thinking_mode: str | None = None
    ) -> dict[str, Any]:
        self.calls += 1
        raise TimeoutError(self.message)


@pytest.fixture
def recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the executor's retry-backoff sleeps instead of waiting.

    Only the executor's own retry backoff calls ``asyncio.sleep`` in the
    tests that use this fixture (``FlakyExecutor`` never sleeps). The
    timeout tests deliberately omit this fixture so ``HangingExecutor``'s
    real sleep is left intact and ``asyncio.wait_for`` actually fires.
    """
    delays: list[float] = []

    async def _record(seconds: float) -> None:
        delays.append(seconds)

    # executor calls ``asyncio.sleep`` (attribute lookup on the asyncio
    # module at call time), so patch the module attribute directly.
    monkeypatch.setattr(asyncio, "sleep", _record)
    return delays


@pytest.mark.asyncio
async def test_step_retries_up_to_max_retries(recorded_sleeps: list[float]) -> None:
    executor = FlakyExecutor(fail_count=2)
    step_def = {
        "id": "s1",
        "tool_type": "system_tool",
        "tool_id": "t1",
        "configuration": {},
        "max_retries": 3,
    }
    node = create_tool_execution_node(step_def=step_def, tool_executor=executor)
    state = WorkflowState(workflow_id="w", execution_id="e", database_name="d")
    result = await node(state)
    assert executor.calls == 3
    assert result.error is None
    assert result.step_results["s1"] == {"ok": True}


@pytest.mark.asyncio
async def test_step_fails_after_exhausting_retries(recorded_sleeps: list[float]) -> None:
    executor = FlakyExecutor(fail_count=5)
    step_def = {
        "id": "s1",
        "tool_type": "system_tool",
        "tool_id": "t1",
        "configuration": {},
        "max_retries": 2,
    }
    node = create_tool_execution_node(step_def=step_def, tool_executor=executor)
    state = WorkflowState(workflow_id="w", execution_id="e", database_name="d")
    result = await node(state)
    # max_retries=2 means 1 initial attempt plus 2 retries, so 3 calls total
    assert executor.calls == 3
    assert result.error is not None
    assert "transient failure" in result.error


@pytest.mark.asyncio
async def test_retry_backoff_delays_between_attempts(recorded_sleeps: list[float]) -> None:
    """Failed attempts wait an exponentially growing, capped delay before retry."""
    executor = FlakyExecutor(fail_count=2)
    step_def = {
        "id": "s1",
        "tool_type": "system_tool",
        "tool_id": "t1",
        "configuration": {},
        "max_retries": 3,
    }
    node = create_tool_execution_node(step_def=step_def, tool_executor=executor)
    state = WorkflowState(workflow_id="w", execution_id="e", database_name="d")
    result = await node(state)
    assert executor.calls == 3
    assert result.error is None
    # 2 failures -> 2 backoff sleeps before the 2 retries; the final
    # (successful) attempt never sleeps. Defaults: base=1.0s, multiplier=2.0
    # -> _backoff_delay(0)=1.0, _backoff_delay(1)=2.0.
    assert recorded_sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_step_timeout_aborts_hung_step_soft_fail() -> None:
    """A step exceeding timeout_seconds is aborted; continue_on_error records it softly."""
    executor = HangingExecutor(delay=0.5)
    step_def = {
        "id": "s1",
        "tool_type": "system_tool",
        "tool_id": "t1",
        "configuration": {},
        "max_retries": 0,
        "timeout_seconds": 0.1,
        "continue_on_error": True,
    }
    node = create_tool_execution_node(step_def=step_def, tool_executor=executor)
    state = WorkflowState(workflow_id="w", execution_id="e", database_name="d")
    result = await node(state)
    assert result.error is None  # soft fail does not poison the workflow
    assert "s1" in result.step_errors
    assert "timed out" in result.step_errors["s1"]
    assert "timed out" in result.step_results["s1"]["error"]


@pytest.mark.asyncio
async def test_step_timeout_hard_fail_sets_state_error() -> None:
    """Without continue_on_error, a timeout fails the workflow with a clear error."""
    executor = HangingExecutor(delay=0.5)
    step_def = {
        "id": "s1",
        "tool_type": "system_tool",
        "tool_id": "t1",
        "configuration": {},
        "max_retries": 0,
        "timeout_seconds": 0.1,
        "continue_on_error": False,
    }
    node = create_tool_execution_node(step_def=step_def, tool_executor=executor)
    state = WorkflowState(workflow_id="w", execution_id="e", database_name="d")
    result = await node(state)
    assert result.error is not None
    assert "timed out" in result.error
    assert result.status == "failed"


@pytest.mark.asyncio
async def test_no_timeout_step_has_no_backoff_on_success(
    recorded_sleeps: list[float],
) -> None:
    """A no-timeout step that succeeds first try never wraps in wait_for or sleeps."""
    executor = FlakyExecutor(fail_count=0)
    step_def = {
        "id": "s1",
        "tool_type": "system_tool",
        "tool_id": "t1",
        "configuration": {},
        "max_retries": 0,
    }
    node = create_tool_execution_node(step_def=step_def, tool_executor=executor)
    state = WorkflowState(workflow_id="w", execution_id="e", database_name="d")
    result = await node(state)
    assert executor.calls == 1
    assert result.error is None
    assert result.step_results["s1"] == {"ok": True}
    assert recorded_sleeps == []


@pytest.mark.asyncio
async def test_tool_raised_timeout_message_preserved() -> None:
    """A TimeoutError raised by the tool itself keeps its message even when a
    step timeout budget is set (the budget did not fire — the tool failed first).
    """
    executor = SelfTimeoutExecutor("UPSTREAM_504 from provider")
    step_def = {
        "id": "s1",
        "tool_type": "system_tool",
        "tool_id": "t1",
        "configuration": {},
        "max_retries": 0,
        "timeout_seconds": 30,  # generous budget; the tool fails instantly
        "continue_on_error": False,
    }
    node = create_tool_execution_node(step_def=step_def, tool_executor=executor)
    state = WorkflowState(workflow_id="w", execution_id="e", database_name="d")
    result = await node(state)
    assert executor.calls == 1
    assert result.error is not None
    # The tool's own message must survive, not be overwritten by the synthesized
    # "timed out after 30s" (which would falsely report a 30s budget overrun).
    assert "UPSTREAM_504 from provider" in result.error


@pytest.mark.asyncio
async def test_timeout_with_retries_applies_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeatedly-timing-out step retries with backoff between attempts, then hard-fails."""
    # Tiny backoff so the (real) inter-attempt sleeps don't slow the test; the
    # exact schedule is asserted in test_retry_backoff_delays_between_attempts.
    fake = SimpleNamespace(
        workflows=SimpleNamespace(
            step_retry_base_delay_seconds=0.001,
            step_retry_max_delay_seconds=1.0,
        ),
        backoff=SimpleNamespace(exponential_multiplier=2.0),
    )
    monkeypatch.setattr(
        "chaoscypher_core.services.workflows.engine.executor.get_settings",
        lambda: fake,
    )
    executor = HangingExecutor(delay=0.5)
    step_def = {
        "id": "s1",
        "tool_type": "system_tool",
        "tool_id": "t1",
        "configuration": {},
        "max_retries": 2,
        "timeout_seconds": 0.05,
        "continue_on_error": False,
    }
    node = create_tool_execution_node(step_def=step_def, tool_executor=executor)
    state = WorkflowState(workflow_id="w", execution_id="e", database_name="d")
    result = await node(state)
    assert executor.calls == 3  # initial attempt + 2 retries, each timing out
    assert result.error is not None
    assert "timed out" in result.error
