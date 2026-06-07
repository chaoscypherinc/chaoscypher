# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LangGraph State Models.

Pydantic models for workflow state management.
Type-safe state transformations for both backend and CLI.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field


def _take_last[T](_left: T, right: T) -> T:
    """LangGraph channel reducer: keep the most recent write.

    Every node returns the whole ``WorkflowState`` back, so each parallel
    branch re-writes the constant metadata (``workflow_id`` …) and the
    last-writer-wins informational fields (``current_step`` …). Without a
    reducer LangGraph's ``LastValue`` channel rejects the second concurrent
    write with ``InvalidUpdateError``.
    """
    return right


def _dict_merge[K, V](left: dict[K, V], right: dict[K, V]) -> dict[K, V]:
    """LangGraph channel reducer: union two dict writes from parallel branches.

    Parallel branches write disjoint keys (each step owns its own ``step_id``),
    so the merge is conflict-free. The union also keeps the single-branch path
    strictly additive — semantically identical to the prior in-place mutation.
    """
    return {**left, **right}


def _keep_error(left: str | None, right: str | None) -> str | None:
    """LangGraph channel reducer: sticky first error.

    The first non-``None`` error wins and a later ``None`` never clears it.
    This is mandatory (not ``_take_last``): a concurrent *successful* sibling
    re-writes ``error=None`` in the same super-step, and ``_take_last`` could
    drop a failed branch's error and silently break fail-stop routing. Sticky +
    order-independent for the "did any branch fail?" invariant.
    """
    return left if left is not None else right


def _keep_failed(left: str, right: str) -> str:
    """LangGraph channel reducer: sticky failure.

    Once any branch reports ``"failed"`` the status stays failed even if a
    concurrent sibling writes ``"running"`` in the same super-step.
    """
    return "failed" if "failed" in (left, right) else right


class WorkflowState(BaseModel):
    """State object for workflow execution.

    LangGraph uses this Pydantic model to manage workflow state.
    All state transformations are type-safe.

    Works in both async (backend) and sync (CLI) modes.

    Every field carries a channel reducer (``Annotated[T, reducer]``) so a real
    DAG with parallel branches can write the state back from each branch in the
    same super-step without tripping LangGraph's one-write-per-step
    ``LastValue`` constraint. See the module-level reducers for the contract.
    """

    # Metadata
    workflow_id: Annotated[str, _take_last] = Field(..., description="Workflow ID")
    execution_id: Annotated[str, _take_last] = Field(..., description="Unique execution ID")
    database_name: Annotated[str, _take_last] = Field(..., description="Database context")

    # Execution state
    current_step: Annotated[str | None, _take_last] = Field(None, description="Current step ID")
    step_results: Annotated[dict[str, Any], _dict_merge] = Field(
        default_factory=dict, description="Results by step ID"
    )
    step_errors: Annotated[dict[str, str], _dict_merge] = Field(
        default_factory=dict, description="Errors by step ID"
    )

    # Input/output
    initial_inputs: Annotated[dict[str, Any], _dict_merge] = Field(
        default_factory=dict, description="Workflow inputs"
    )
    final_output: Annotated[Any | None, _take_last] = Field(
        None, description="Final workflow result"
    )

    # Status
    status: Annotated[str, _keep_failed] = Field(default="running", description="Workflow status")
    error: Annotated[str | None, _keep_error] = Field(
        None, description="Global error if workflow failed"
    )

    # Timestamps
    started_at: Annotated[datetime, _take_last] = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: Annotated[datetime | None, _take_last] = None

    class Config:
        """Pydantic model configuration."""

        arbitrary_types_allowed = True  # Allow injected services


__all__ = ["WorkflowState"]
