# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for fatal-error propagation from orchestrator (finding #12)."""

from typing import Any

import pytest

from chaoscypher_core.exceptions import WorkflowExecutionError
from chaoscypher_core.operations.workflows.orchestrator import (
    execute_workflow_task,
)


class RaisingToolService:
    def list_system_tools(self) -> list[dict[str, Any]]:
        return [{"id": "tool_x"}]

    def list_user_tools(self) -> list[dict[str, Any]]:
        return []

    def get_user_tool(self, tid: str) -> Any:
        return None


class OneStepWorkflow:
    def get_workflow(self, wid: str) -> dict[str, Any]:
        return {
            "id": wid,
            "name": "x",
            "is_active": True,
            "output_schema": None,
            "input_schema": {"type": "object"},
            "max_retries": 0,
        }

    def list_workflow_steps(self, wid: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "s1",
                "step_number": 1,
                "name": "Step1",
                "tool_type": "system_tool",
                "tool_id": "tool_x",
                "configuration": {},
                "continue_on_error": False,
            }
        ]


@pytest.mark.asyncio
async def test_fatal_step_error_raises_workflow_execution_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stub the execution repo so we don't need a real DB.
    import chaoscypher_core.operations.workflows.orchestrator as orch

    class FakeRepo:
        def get_execution(self, eid: str) -> dict | None:
            return None

        def create_execution(self, data: dict) -> None: ...

        def update_status(self, eid: str, status: str) -> None: ...

        def finalize_execution(self, *args: Any, **kw: Any) -> None: ...

    monkeypatch.setattr(orch, "WorkflowExecutionRepository", lambda db: FakeRepo())

    class FailingAdapter:
        async def execute_tool(self, **kw: Any) -> dict:
            raise RuntimeError("kaboom")

    monkeypatch.setattr(
        orch,
        "BackendToolExecutorAdapter",
        lambda **kw: FailingAdapter(),
    )

    with pytest.raises(WorkflowExecutionError, match="kaboom"):
        await execute_workflow_task(
            workflow_id="w",
            inputs={},
            workflow_service=OneStepWorkflow(),
            tool_service=RaisingToolService(),
            llm_service=None,
            graph_repository=None,  # type: ignore[arg-type]
            search_repository=None,
            database_name="default",
        )
