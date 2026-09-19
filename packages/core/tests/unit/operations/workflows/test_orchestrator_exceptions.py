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

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import NotFoundError, ValidationError
from chaoscypher_core.operations.workflows.orchestrator import execute_workflow_task
from chaoscypher_core.operations.workflows.status import WorkflowExecutionStatus


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


class TestAdapterCleanupOnEarlyExit:
    """The exec adapter session must not leak on pre-execution exit paths.

    The ``finally: exec_adapter.disconnect()`` guards only the execution
    block; validation raises exit earlier and previously leaked the
    adapter's session (workers never take the request-context cleanup
    branch in ``get_sqlite_adapter``).
    """

    @pytest.mark.asyncio
    async def test_disconnects_when_workflow_missing(self) -> None:
        from unittest.mock import patch

        adapter = MagicMock()
        workflow_service = _make_workflow_service(workflow=None)

        with (
            patch(
                "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
                return_value=adapter,
            ),
            pytest.raises(NotFoundError),
        ):
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

        adapter.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnects_when_workflow_inactive(self) -> None:
        from unittest.mock import patch

        adapter = MagicMock()
        workflow = {
            "id": "wf-1",
            "name": "wf",
            "is_active": False,
        }
        workflow_service = _make_workflow_service(workflow=workflow)

        with (
            patch(
                "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
                return_value=adapter,
            ),
            pytest.raises(ValidationError),
        ):
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

        adapter.disconnect.assert_called_once()


class TestCancellationFinalizesExecution:
    """A cancel must not leave the durable execution row RUNNING forever."""

    @pytest.mark.asyncio
    async def test_cancel_during_invoke_finalizes_row_and_reraises(self) -> None:
        """Worker drain / task timeout cancels are routine, not exotic.

        ``CancelledError`` is a ``BaseException``, so the handler's
        ``except Exception`` arm never saw it. Nothing else writes
        ``workflow_executions`` and no reconciler covers that table, so the
        row stayed ``running`` with NULL ``completed_at`` for the life of the
        database — and with ``allow_parallel_execution=False`` it then raised
        ``WorkflowBusyError`` on every future run of that workflow.
        """
        from unittest.mock import patch

        from chaoscypher_core.operations.workflows.repository import (
            WorkflowExecutionRepository,
        )
        from chaoscypher_core.services.workflows.engine.validator import WorkflowValidator

        workflow = {"id": "wf-1", "name": "Test", "is_active": True, "steps": []}
        workflow_service = _make_workflow_service(workflow=workflow)
        workflow_service.list_workflow_steps.return_value = [
            {"step_number": 1, "tool_type": "system_tool", "tool_id": "t1"}
        ]

        compiled = MagicMock()
        compiled.ainvoke = AsyncMock(side_effect=asyncio.CancelledError())
        graph = MagicMock()
        graph.compile.return_value = compiled

        with (
            patch(
                "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
                return_value=MagicMock(),
            ),
            patch.object(WorkflowValidator, "validate_workflow", staticmethod(lambda w: [])),
            patch.object(WorkflowValidator, "validate_inputs", staticmethod(lambda w, i: [])),
            patch.object(WorkflowExecutionRepository, "create_execution", return_value="exec-1"),
            patch.object(WorkflowExecutionRepository, "update_status"),
            patch.object(WorkflowExecutionRepository, "finalize_execution") as finalize,
            patch(
                "chaoscypher_core.operations.workflows.orchestrator.build_workflow_graph",
                return_value=graph,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
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

        finalize.assert_called_once()
        assert finalize.call_args.kwargs["status"] == WorkflowExecutionStatus.FAILED
        assert "cancelled" in finalize.call_args.kwargs["error_message"].lower()
