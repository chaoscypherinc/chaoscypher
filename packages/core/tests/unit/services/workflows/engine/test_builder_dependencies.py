# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow graph builder dependency-order regression tests."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.workflows.engine.builder import build_workflow_graph
from chaoscypher_core.services.workflows.engine.state import WorkflowState


class RecordingToolExecutor:
    """Minimal ToolExecutor that records execution order and interpolated inputs.

    Optional knobs let one harness drive the fan-out tests:

    - ``barrier`` + ``barrier_ids``: tools whose ids are in ``barrier_ids`` await
      the shared ``asyncio.Barrier`` before returning. The barrier only releases
      once *every* party arrives at the same time, so a serialized builder blocks
      forever (the caller's ``asyncio.wait_for`` then trips) while a fan-out
      builder runs them concurrently and proceeds. This turns "did they run in
      parallel?" into a deterministic pass/fail instead of a timing race.
    - ``fail_ids``: tools whose ids are in ``fail_ids`` raise ``RuntimeError`` to
      exercise error / fail-stop routing.
    """

    def __init__(
        self,
        *,
        barrier: asyncio.Barrier | None = None,
        barrier_ids: set[str] | None = None,
        fail_ids: set[str] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._barrier = barrier
        self._barrier_ids = barrier_ids or set()
        self._fail_ids = fail_ids or set()

    async def execute_tool(
        self,
        tool_id: str,
        inputs: dict[str, Any],
        thinking_mode: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((tool_id, inputs))
        if self._barrier is not None and tool_id in self._barrier_ids:
            await self._barrier.wait()
        if tool_id in self._fail_ids:
            msg = f"{tool_id} boom"
            raise RuntimeError(msg)
        return {"value": tool_id, "inputs": inputs}


def _step(
    step_id: str,
    *,
    step_number: int,
    depends_on: list[str] | None = None,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": step_id,
        "name": step_id,
        "step_number": step_number,
        "tool_type": "system_tool",
        "tool_id": step_id,
        "configuration": configuration or {},
        "depends_on": depends_on or [],
        "continue_on_error": False,
    }


@pytest.mark.asyncio
async def test_builder_executes_depends_on_order_when_list_order_diverges() -> None:
    """A dependent step must not run before the step it references."""
    tool_executor = RecordingToolExecutor()
    workflow_def = {
        "id": "wf-deps",
        "name": "Dependency workflow",
        "steps": [
            _step(
                "c",
                step_number=1,
                depends_on=["b"],
                configuration={"from_b": "{{steps.b.value}}"},
            ),
            _step("a", step_number=2),
            _step(
                "b",
                step_number=3,
                depends_on=["a"],
                configuration={"from_a": "{{steps.a.value}}"},
            ),
        ],
    }

    compiled = build_workflow_graph(workflow_def, tool_executor).compile()
    final_state = await compiled.ainvoke(
        WorkflowState(workflow_id="wf-deps", execution_id="exec-1", database_name="db")
    )

    assert [tool_id for tool_id, _inputs in tool_executor.calls] == ["a", "b", "c"]
    assert tool_executor.calls[1][1] == {"from_a": "a"}
    assert tool_executor.calls[2][1] == {"from_b": "b"}
    assert final_state["step_results"]["c"]["inputs"] == {"from_b": "b"}


@pytest.mark.asyncio
async def test_builder_joins_multiple_dependencies_in_deterministic_order() -> None:
    """A join step runs after all its upstreams and sees every upstream output.

    The builder fans independent branches out in parallel, so the order of
    ``left`` vs ``right`` is not fixed. The invariant is dependency-respecting,
    not positional: the join runs after both branches, exactly once, with both
    interpolated inputs present.
    """
    tool_executor = RecordingToolExecutor()
    workflow_def = {
        "id": "wf-join",
        "name": "Join workflow",
        "steps": [
            _step(
                "join",
                step_number=1,
                depends_on=["left", "right"],
                configuration={
                    "left": "{{steps.left.value}}",
                    "right": "{{steps.right.value}}",
                },
            ),
            _step("right", step_number=3),
            _step("left", step_number=2),
        ],
    }

    compiled = build_workflow_graph(workflow_def, tool_executor).compile()
    final_state = await compiled.ainvoke(
        WorkflowState(workflow_id="wf-join", execution_id="exec-2", database_name="db")
    )

    called = [tool_id for tool_id, _inputs in tool_executor.calls]
    assert called.count("join") == 1
    assert called.index("join") > called.index("left")
    assert called.index("join") > called.index("right")
    assert final_state["step_results"]["join"]["inputs"] == {"left": "left", "right": "right"}


@pytest.mark.asyncio
async def test_builder_runs_independent_branches_in_parallel() -> None:
    """Independent branches execute concurrently, not one-after-another.

    Proven deterministically rather than by timing: ``left`` and ``right`` each
    await a shared ``asyncio.Barrier(2)``. A serialized builder runs one before
    the other, so the first blocks on the barrier forever and ``asyncio.wait_for``
    trips — turning a non-parallel builder into a loud failure. A fan-out builder
    schedules both in the same super-step, the barrier releases, and the join
    observes both upstream values.
    """
    barrier = asyncio.Barrier(2)
    tool_executor = RecordingToolExecutor(barrier=barrier, barrier_ids={"left", "right"})
    workflow_def = {
        "id": "wf-fanout",
        "name": "Fan-out workflow",
        "steps": [
            _step("root", step_number=1),
            _step("left", step_number=2, depends_on=["root"]),
            _step("right", step_number=3, depends_on=["root"]),
            _step(
                "join",
                step_number=4,
                depends_on=["left", "right"],
                configuration={
                    "left": "{{steps.left.value}}",
                    "right": "{{steps.right.value}}",
                },
            ),
        ],
    }

    compiled = build_workflow_graph(workflow_def, tool_executor).compile()
    final_state = await asyncio.wait_for(
        compiled.ainvoke(
            WorkflowState(workflow_id="wf-fanout", execution_id="exec-3", database_name="db")
        ),
        timeout=5.0,
    )

    called = [tool_id for tool_id, _inputs in tool_executor.calls]
    assert called.count("join") == 1
    assert called.index("join") > called.index("left")
    assert called.index("join") > called.index("right")
    assert final_state["step_results"]["join"]["inputs"] == {"left": "left", "right": "right"}


@pytest.mark.asyncio
async def test_builder_join_runs_once_for_asymmetric_dag() -> None:
    """A join depending on an ancestor *and* that ancestor's descendant runs once.

    For ``d`` depending on ``{a, b}`` where ``b`` depends on ``a``, the join must
    wait for both upstreams and execute exactly once. A naive list-returning
    fan-out router would fan ``a`` out to ``[b, d]`` and run ``d`` prematurely
    (twice); the AND-join wiring gates ``d`` on the union of its upstreams.
    """
    tool_executor = RecordingToolExecutor()
    workflow_def = {
        "id": "wf-asym",
        "name": "Asymmetric workflow",
        "steps": [
            _step("a", step_number=1),
            _step("b", step_number=2, depends_on=["a"]),
            _step(
                "d",
                step_number=3,
                depends_on=["a", "b"],
                configuration={"a": "{{steps.a.value}}", "b": "{{steps.b.value}}"},
            ),
        ],
    }

    compiled = build_workflow_graph(workflow_def, tool_executor).compile()
    final_state = await compiled.ainvoke(
        WorkflowState(workflow_id="wf-asym", execution_id="exec-4", database_name="db")
    )

    called = [tool_id for tool_id, _inputs in tool_executor.calls]
    assert called.count("d") == 1
    assert called.index("d") > called.index("b")
    assert final_state["step_results"]["d"]["inputs"] == {"a": "a", "b": "b"}


@pytest.mark.asyncio
async def test_builder_fan_out_fails_stop_on_branch_error() -> None:
    """A failed branch (not continue_on_error) stops the workflow fail-stop.

    When ``left`` fails, the downstream join's tool must NOT execute (the
    poison-pill guard skips it rather than run with a missing upstream), and the
    workflow ends ``failed`` with the error surfaced and recorded per step.
    """
    tool_executor = RecordingToolExecutor(fail_ids={"left"})
    workflow_def = {
        "id": "wf-failstop",
        "name": "Fail-stop workflow",
        "steps": [
            _step("root", step_number=1),
            _step("left", step_number=2, depends_on=["root"]),
            _step("right", step_number=3, depends_on=["root"]),
            _step(
                "join",
                step_number=4,
                depends_on=["left", "right"],
                configuration={"left": "{{steps.left.value}}"},
            ),
        ],
    }

    compiled = build_workflow_graph(workflow_def, tool_executor).compile()
    final_state = await compiled.ainvoke(
        WorkflowState(workflow_id="wf-failstop", execution_id="exec-5", database_name="db")
    )

    called = [tool_id for tool_id, _inputs in tool_executor.calls]
    assert "join" not in called
    assert final_state["status"] == "failed"
    assert final_state["error"]
    assert "left" in final_state["step_errors"]


@pytest.mark.asyncio
async def test_builder_linear_workflow_runs_sequentially_unchanged() -> None:
    """A purely linear workflow behaves exactly as before: strict order, running."""
    tool_executor = RecordingToolExecutor()
    workflow_def = {
        "id": "wf-linear",
        "name": "Linear workflow",
        "steps": [
            _step("a", step_number=1),
            _step("b", step_number=2, depends_on=["a"], configuration={"a": "{{steps.a.value}}"}),
            _step("c", step_number=3, depends_on=["b"], configuration={"b": "{{steps.b.value}}"}),
        ],
    }

    compiled = build_workflow_graph(workflow_def, tool_executor).compile()
    final_state = await compiled.ainvoke(
        WorkflowState(workflow_id="wf-linear", execution_id="exec-6", database_name="db")
    )

    assert [tool_id for tool_id, _inputs in tool_executor.calls] == ["a", "b", "c"]
    assert final_state["status"] == "running"
    assert set(final_state["step_results"]) == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_builder_continue_on_error_branch_does_not_stop_fan_out() -> None:
    """A soft-failing (continue_on_error) branch lets the rest of the DAG proceed."""
    tool_executor = RecordingToolExecutor(fail_ids={"left"})
    left = _step("left", step_number=2, depends_on=["root"])
    left["continue_on_error"] = True
    workflow_def = {
        "id": "wf-soft",
        "name": "Soft-fail workflow",
        "steps": [
            _step("root", step_number=1),
            left,
            _step("right", step_number=3, depends_on=["root"]),
            _step("join", step_number=4, depends_on=["left", "right"]),
        ],
    }

    compiled = build_workflow_graph(workflow_def, tool_executor).compile()
    final_state = await compiled.ainvoke(
        WorkflowState(workflow_id="wf-soft", execution_id="exec-7", database_name="db")
    )

    called = [tool_id for tool_id, _inputs in tool_executor.calls]
    assert "join" in called
    assert final_state["status"] == "running"
    assert final_state.get("error") is None


def test_builder_rejects_unknown_dependency_when_called_directly() -> None:
    """Direct builder callers get the same dependency safety as validator callers."""
    workflow_def = {
        "id": "wf-bad",
        "name": "Bad workflow",
        "steps": [_step("a", step_number=1, depends_on=["ghost"])],
    }

    with pytest.raises(ValidationError, match="ghost"):
        build_workflow_graph(workflow_def, RecordingToolExecutor())


def test_builder_rejects_dependency_cycle() -> None:
    """Cyclic depends_on must surface as a ValidationError instead of silent ordering loss."""
    workflow_def = {
        "id": "wf-cycle",
        "name": "Cyclic workflow",
        "steps": [
            _step("a", step_number=1, depends_on=["b"]),
            _step("b", step_number=2, depends_on=["c"]),
            _step("c", step_number=3, depends_on=["a"]),
        ],
    }

    with pytest.raises(ValidationError, match="cycle"):
        build_workflow_graph(workflow_def, RecordingToolExecutor())


def test_builder_rejects_duplicate_step_id() -> None:
    """Two steps with the same id would silently collapse — the builder must reject."""
    workflow_def = {
        "id": "wf-dup",
        "name": "Duplicate id workflow",
        "steps": [
            _step("a", step_number=1),
            _step("a", step_number=2),
        ],
    }

    with pytest.raises(ValidationError, match="Duplicate"):
        build_workflow_graph(workflow_def, RecordingToolExecutor())


def test_builder_rejects_step_missing_id() -> None:
    """A step with no id can't be referenced or wired — must fail loudly."""
    workflow_def = {
        "id": "wf-noid",
        "name": "Missing id workflow",
        "steps": [
            {
                "name": "anonymous",
                "step_number": 1,
                "tool_type": "system_tool",
                "tool_id": "noop",
                "configuration": {},
                "depends_on": [],
                "continue_on_error": False,
            }
        ],
    }

    with pytest.raises(ValidationError, match="Missing 'id'"):
        build_workflow_graph(workflow_def, RecordingToolExecutor())
