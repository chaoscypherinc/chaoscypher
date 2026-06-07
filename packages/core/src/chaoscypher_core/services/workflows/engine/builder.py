# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LangGraph Workflow Builder.

Builds StateGraph from workflow definitions.
Works in both backend (async) and CLI (sync if using langgraph-sync fork).
"""

from collections import deque
from collections.abc import Callable
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.workflows.engine.executor import (
    ToolExecutor,
    WorkflowExecutor,
    create_error_handler_node,
    create_tool_execution_node,
)
from chaoscypher_core.services.workflows.engine.state import WorkflowState


logger = structlog.get_logger(__name__)


def build_workflow_graph(
    workflow_def: dict[str, Any],
    tool_executor: ToolExecutor,
    workflow_executor: WorkflowExecutor | None = None,
    user_tool_resolver: Callable | None = None,
) -> StateGraph:
    """Build LangGraph StateGraph from workflow definition.

    This function works in both backend and CLI modes by accepting
    Protocol-based executors.

    Args:
        workflow_def: Workflow definition dict containing:
            - id: Workflow identifier
            - name: Workflow name
            - steps: List of workflow steps
        tool_executor: ToolExecutor implementation (backend or CLI)
        workflow_executor: Optional WorkflowExecutor for nested workflows
        user_tool_resolver: Optional function(tool_id) -> user_tool_dict

    Returns:
        StateGraph ready for compilation

    Raises:
        ValidationError: If the workflow definition contains no steps.

    Example (Backend):
        >>> from backend.adapters import BackendToolExecutor
        >>> executor = BackendToolExecutor(services)
        >>> graph = build_workflow_graph(workflow_dict, executor)
        >>> compiled = graph.compile()
        >>> result = await compiled.ainvoke(initial_state)

    Example (CLI):
        >>> from cli.executors import CLIToolExecutor
        >>> executor = CLIToolExecutor(graph_repo, tool_executor)
        >>> graph = build_workflow_graph(workflow_dict, executor)
        >>> compiled = graph.compile()
        >>> result = await compiled.ainvoke(initial_state)

    """
    logger.info(
        "workflow_graph_building",
        workflow_name=workflow_def.get("name", "unknown"),
        workflow_id=workflow_def.get("id"),
    )

    # Create state graph
    graph = StateGraph(WorkflowState)

    # Get workflow steps
    steps = workflow_def.get("steps", [])

    if not steps:
        msg = "Workflow has no steps"
        raise ValidationError(msg)

    steps = _order_steps_by_dependencies(steps)

    # Workflow-level retry default, overridable per step
    workflow_max_retries = int(workflow_def.get("max_retries", 0))

    # Add nodes for each step
    for step in steps:
        step_id = step["id"]
        step_name = step.get("name", step_id)

        # Thread workflow-level max_retries into each step copy (per-step override wins)
        step_with_retries = {
            **step,
            "max_retries": step.get("max_retries", workflow_max_retries),
        }

        # Create tool execution node (handles all tool types)
        node_fn = create_tool_execution_node(
            step_def=step_with_retries,
            tool_executor=tool_executor,
            workflow_executor=workflow_executor,
            user_tool_resolver=user_tool_resolver,
        )
        graph.add_node(step_id, node_fn)

        logger.debug(
            "workflow_node_added",
            step_id=step_id,
            step_name=step_name,
            tool_type=step.get("tool_type"),
            max_retries=step_with_retries["max_retries"],
        )

    # Add error handler node
    graph.add_node("error_handler", create_error_handler_node())

    # Wire control flow as a real DAG instead of a linear chain.
    #
    # Each step's incoming edges form a LangGraph AND-join over its declared
    # dependencies: ``add_edge([parents], step)`` triggers the step only after
    # *every* parent has completed in the same run. Fan-out is therefore
    # implicit — sibling steps that share a parent are scheduled together and
    # run concurrently — while a join is gated on the union of its upstreams, so
    # it runs exactly once with all inputs present. A purely linear workflow
    # collapses to single-parent joins and behaves identically to the previous
    # sequential engine. Channel reducers on WorkflowState (see state.py) make
    # the concurrent state write-back safe.
    parents_by_id: dict[str, list[str]] = {
        step["id"]: list(step.get("depends_on") or []) for step in steps
    }
    dependents_by_id: dict[str, list[str]] = {step["id"]: [] for step in steps}
    for step_id, parent_ids in parents_by_id.items():
        for parent_id in parent_ids:
            dependents_by_id[parent_id].append(step_id)

    # Incoming AND-join edges. ``sorted`` keeps wiring deterministic; the AND
    # semantics are order-independent at run time.
    for step in steps:
        step_id = step["id"]
        parent_ids = sorted(parents_by_id[step_id])
        if parent_ids:
            graph.add_edge(parent_ids, step_id)
            logger.debug("workflow_join_edge_added", to_step=step_id, from_steps=parent_ids)

    # Terminal steps (nothing depends on them) route to END on success or to the
    # shared error handler on a hard failure — same contract as the sequential
    # engine. Non-terminal steps need no outgoing edge here: their dependents
    # pull them in via the AND-join above. Fail-stop after an upstream failure is
    # enforced by the executor's poison-pill guard, not by per-step error edges.
    for step in steps:
        step_id = step["id"]
        if dependents_by_id[step_id]:
            continue
        graph.add_conditional_edges(
            step_id, _should_continue, {"continue": END, "error": "error_handler"}
        )
        logger.debug(
            "workflow_terminal_edge_added", from_step=step_id, error_handler="error_handler"
        )

    # Error handler always ends
    graph.add_edge("error_handler", END)

    # Entry-point wiring: a single zero-dependency step keeps the legacy
    # set_entry_point shape; multiple zero-dependency steps fan out from START.
    entry_step_ids = [step["id"] for step in steps if not parents_by_id[step["id"]]]
    if len(entry_step_ids) == 1:
        graph.set_entry_point(entry_step_ids[0])
    else:
        for entry_step_id in entry_step_ids:
            graph.add_edge(START, entry_step_id)

    logger.info(
        "workflow_graph_built",
        workflow_id=workflow_def.get("id"),
        step_count=len(steps),
        has_nested_workflows=workflow_executor is not None,
        has_user_tools=user_tool_resolver is not None,
    )

    return graph


def _should_continue(state: WorkflowState) -> str:
    """Decide whether to continue or handle error.

    This function is called by conditional edges.

    Args:
        state: Current workflow state

    Returns:
        "continue" if no error, "error" if error occurred

    """
    if state.error:
        return "error"
    return "continue"


def _order_steps_by_dependencies(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return steps in dependency-safe topological order.

    The interface stores canvas edges in ``depends_on`` and assigns
    ``step_number`` in topological order, but workflows can also arrive
    from imports/API calls where the list order diverges. The LangGraph
    builder is the last line of defense before execution, so it honors
    ``depends_on`` directly instead of trusting incoming list order.
    """
    step_by_id: dict[str, dict[str, Any]] = {}
    original_index: dict[str, int] = {}
    for idx, step in enumerate(steps):
        step_id = step.get("id")
        if not step_id:
            msg = f"Step {idx}: Missing 'id' field"
            raise ValidationError(msg)
        if step_id in step_by_id:
            msg = f"Duplicate workflow step id: {step_id}"
            raise ValidationError(msg)
        step_by_id[step_id] = step
        original_index[step_id] = idx

    adjacency: dict[str, list[str]] = {step_id: [] for step_id in step_by_id}
    in_degree: dict[str, int] = dict.fromkeys(step_by_id, 0)

    for step in steps:
        step_id = step["id"]
        for dependency_id in step.get("depends_on") or []:
            if dependency_id not in step_by_id:
                msg = f"Step '{step_id}' depends_on references unknown step '{dependency_id}'"
                raise ValidationError(msg)
            adjacency[dependency_id].append(step_id)
            in_degree[step_id] += 1

    def sort_key(step_id: str) -> tuple[int, int]:
        step_number = step_by_id[step_id].get("step_number")
        if not isinstance(step_number, int):
            step_number = original_index[step_id]
        return (
            step_number,
            original_index[step_id],
        )

    ready = deque(sorted((sid for sid, degree in in_degree.items() if degree == 0), key=sort_key))
    ordered_ids: list[str] = []

    while ready:
        step_id = ready.popleft()
        ordered_ids.append(step_id)
        for successor_id in sorted(adjacency[step_id], key=sort_key):
            in_degree[successor_id] -= 1
            if in_degree[successor_id] == 0:
                ready.append(successor_id)

    if len(ordered_ids) != len(step_by_id):
        cyclic = sorted(step_id for step_id, degree in in_degree.items() if degree > 0)
        msg = f"Dependency cycle detected involving steps: {', '.join(cyclic)}"
        raise ValidationError(msg)

    return [step_by_id[step_id] for step_id in ordered_ids]


__all__ = ["build_workflow_graph"]
