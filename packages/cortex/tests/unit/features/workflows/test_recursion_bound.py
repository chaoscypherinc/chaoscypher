# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for nested-workflow recursion bound (finding #6)."""

import pytest

from chaoscypher_core.exceptions import WorkflowRecursionError
from chaoscypher_core.operations.workflows.orchestrator import (
    _WorkflowExecutorWrapper,
)


class FakeWorkflowService:
    def get_workflow(self, wid: str) -> dict:
        return {"id": wid, "name": wid, "is_active": True, "output_schema": None}

    def list_workflow_steps(self, wid: str) -> list:
        return []


@pytest.mark.asyncio
async def test_rejects_self_reference() -> None:
    wrapper = _WorkflowExecutorWrapper(
        workflow_service=FakeWorkflowService(),
        tool_service=None,
        llm_service=None,
        graph_repository=None,  # type: ignore[arg-type]
        search_repository=None,
        database_name="default",
        depth=0,
        lineage=frozenset({"wf_a"}),
    )
    with pytest.raises(WorkflowRecursionError, match="cycle"):
        await wrapper.execute_workflow("wf_a", {})


@pytest.mark.asyncio
async def test_rejects_over_max_depth() -> None:
    wrapper = _WorkflowExecutorWrapper(
        workflow_service=FakeWorkflowService(),
        tool_service=None,
        llm_service=None,
        graph_repository=None,  # type: ignore[arg-type]
        search_repository=None,
        database_name="default",
        depth=10,
        lineage=frozenset(),
        max_recursion_depth=10,
    )
    with pytest.raises(WorkflowRecursionError, match="depth"):
        await wrapper.execute_workflow("wf_deep", {})
