# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests: execute_workflow_task raises Core exceptions, not stdlib types.

Covers:
- Workflow not found → NotFoundError
- Workflow validation errors → ValidationError
- Input validation errors → ValidationError
- Inactive workflow → ValidationError
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import NotFoundError, ValidationError
from chaoscypher_core.operations.workflows.orchestrator import execute_workflow_task


def _make_graph_repo() -> MagicMock:
    return MagicMock()


def _make_workflow_service(workflow: dict | None) -> MagicMock:
    svc = MagicMock()
    svc.get_workflow.return_value = workflow
    svc.list_workflow_steps.return_value = []
    return svc


class TestWorkflowNotFound:
    """NotFoundError raised when the workflow does not exist."""

    @pytest.mark.asyncio
    async def test_raises_not_found_error(self) -> None:
        workflow_service = _make_workflow_service(workflow=None)

        with pytest.raises(NotFoundError) as exc_info:
            await execute_workflow_task(
                workflow_id="wf-missing",
                inputs={},
                workflow_service=workflow_service,
                tool_service=None,
                llm_service=AsyncMock(),
                graph_repository=_make_graph_repo(),
                search_repository=MagicMock(),
                database_name="test_db",
            )

        assert exc_info.value.code == "NOT_FOUND"
        assert "wf-missing" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_not_stdlib_value_error(self) -> None:
        """Confirm stdlib ValueError is no longer raised."""
        workflow_service = _make_workflow_service(workflow=None)

        with pytest.raises(NotFoundError):
            await execute_workflow_task(
                workflow_id="absent",
                inputs={},
                workflow_service=workflow_service,
                tool_service=None,
                llm_service=AsyncMock(),
                graph_repository=_make_graph_repo(),
                search_repository=MagicMock(),
                database_name="test_db",
            )


class TestInactiveWorkflow:
    """ValidationError raised when workflow is not active."""

    @pytest.mark.asyncio
    async def test_raises_validation_error_for_inactive_workflow(self) -> None:
        from chaoscypher_core.services.workflows.engine.validator import WorkflowValidator

        workflow = {
            "id": "wf-1",
            "name": "Test",
            "is_active": False,
            "steps": [],
        }
        workflow_service = _make_workflow_service(workflow=workflow)
        workflow_service.list_workflow_steps.return_value = [
            {"step_number": 1, "tool_type": "system_tool", "tool_id": "t1"}
        ]

        # Patch validator to return no errors so we reach the is_active check
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(WorkflowValidator, "validate_workflow", staticmethod(lambda w: []))
            mp.setattr(WorkflowValidator, "validate_inputs", staticmethod(lambda w, i: []))

            with pytest.raises(ValidationError) as exc_info:
                await execute_workflow_task(
                    workflow_id="wf-1",
                    inputs={},
                    workflow_service=workflow_service,
                    tool_service=None,
                    llm_service=AsyncMock(),
                    graph_repository=_make_graph_repo(),
                    search_repository=MagicMock(),
                    database_name="test_db",
                )

        assert exc_info.value.code == "VALIDATION_ERROR"
        assert "not active" in exc_info.value.message
