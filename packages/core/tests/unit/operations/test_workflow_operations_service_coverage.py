# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage tests for WorkflowOperationsService.

Target: chaoscypher_core.operations.workflow_operations_service

Exercises constructor wiring, handler registration, and both async handlers
(_workflow_handler with success/failure/exception/idempotency-skip branches,
and _step_handler with success/exception branches). Orchestrator functions
and repositories are patched at their source paths; collaborators are mocks.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.operations.workflow_operations_service import (
    WorkflowOperationsService,
)


def _make_service() -> WorkflowOperationsService:
    return WorkflowOperationsService(
        workflow_service=MagicMock(),
        tool_service=MagicMock(),
        llm_service=MagicMock(),
        graph_repository=MagicMock(),
        search_repository=MagicMock(),
        database_name="db-test",
    )


class TestInitAndRegistration:
    def test_services_dict_populated(self) -> None:
        service = _make_service()
        assert service.services["database_name"] == "db-test"
        assert "workflow_service" in service.services
        assert "search_repository" in service.services

    def test_handlers_present_with_retry_on_crash(self) -> None:
        service = _make_service()
        assert set(service.operation_handlers) == {"execute_workflow", "execute_step"}
        for spec in service.operation_handlers.values():
            assert spec.retry_on_crash is True

    def test_register_handlers_delegates(self) -> None:
        service = _make_service()
        mock_client = MagicMock()
        with patch(
            "chaoscypher_core.operations.workflow_operations_service.queue_client",
            mock_client,
        ):
            service.register_handlers()
        mock_client.register_handlers.assert_called_once()


def _patch_workflow_handler(
    *,
    orchestrator_result: dict[str, Any] | None = None,
    orchestrator_exc: Exception | None = None,
    existing_execution: dict[str, Any] | None = None,
) -> tuple[list[Any], MagicMock, MagicMock]:
    """Patch the lazy imports inside _workflow_handler.

    Returns (context-managers, mock_event_bus, mock_exec_task).
    """
    mock_adapter = MagicMock()
    mock_get_adapter = MagicMock(return_value=mock_adapter)

    repo = MagicMock()
    repo.get_execution.return_value = existing_execution
    mock_repo_cls = MagicMock(return_value=repo)

    if orchestrator_exc is not None:
        mock_exec_task = AsyncMock(side_effect=orchestrator_exc)
    else:
        mock_exec_task = AsyncMock(return_value=orchestrator_result or {})

    mock_event_bus = MagicMock()

    cms = [
        patch(
            "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
            mock_get_adapter,
        ),
        patch(
            "chaoscypher_core.operations.workflows.repository.WorkflowExecutionRepository",
            mock_repo_cls,
        ),
        patch(
            "chaoscypher_core.operations.workflows.orchestrator.execute_workflow_task",
            mock_exec_task,
        ),
        patch(
            "chaoscypher_core.operations.workflow_operations_service.event_bus",
            mock_event_bus,
        ),
    ]
    return cms, mock_event_bus, mock_exec_task


class TestWorkflowHandlerSuccess:
    @pytest.mark.asyncio
    async def test_success_emits_completed_and_maps_output(self) -> None:
        service = _make_service()
        cms, mock_event_bus, mock_exec_task = _patch_workflow_handler(
            orchestrator_result={
                "success": True,
                "step_outputs": {"s1": {"v": 1}},
                "outputs": {"final": "ok"},
                "error": None,
            },
        )
        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            result = await service._workflow_handler(
                data={"workflow_id": "wf1", "inputs": {"q": "hi"}},
                metadata={"triggered_by": "schedule"},
                task_id="t",
            )

        assert result["success"] is True
        assert result["step_outputs"] == {"s1": {"v": 1}}
        # orchestrator "outputs" mapped to "output".
        assert result["output"] == {"final": "ok"}

        # triggered_by passed through from metadata.
        _, kwargs = mock_exec_task.call_args
        assert kwargs["triggered_by"] == "schedule"
        assert kwargs["workflow_id"] == "wf1"

        ev_args, _ = mock_event_bus.emit.call_args
        assert ev_args[0] == "task_completed"

    @pytest.mark.asyncio
    async def test_default_triggered_by_when_no_metadata(self) -> None:
        service = _make_service()
        cms, _bus, mock_exec_task = _patch_workflow_handler(
            orchestrator_result={"success": True, "outputs": {}},
        )
        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            await service._workflow_handler(
                data={"workflow_id": "wf2", "inputs": {}},
                metadata=None,
            )
        _, kwargs = mock_exec_task.call_args
        assert kwargs["triggered_by"] == "manual"


class TestWorkflowHandlerFailure:
    @pytest.mark.asyncio
    async def test_unsuccessful_result_emits_task_failed(self) -> None:
        service = _make_service()
        cms, mock_event_bus, _task = _patch_workflow_handler(
            orchestrator_result={
                "success": False,
                "outputs": {},
                "error": "boom",
            },
        )
        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            result = await service._workflow_handler(
                data={"workflow_id": "wf3", "inputs": {}},
            )
        assert result["success"] is False
        assert result["error"] == "boom"
        ev_args, ev_kwargs = mock_event_bus.emit.call_args
        assert ev_args[0] == "task_failed"
        assert ev_kwargs["reason"] == "boom"

    @pytest.mark.asyncio
    async def test_orchestrator_exception_returns_error_dict(self) -> None:
        service = _make_service()
        cms, mock_event_bus, _task = _patch_workflow_handler(
            orchestrator_exc=RuntimeError("kaboom"),
        )
        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            result = await service._workflow_handler(
                data={"workflow_id": "wf4", "inputs": {}},
            )
        assert result["success"] is False
        assert result["output"] == {}
        assert "kaboom" in result["error"]
        ev_args, ev_kwargs = mock_event_bus.emit.call_args
        assert ev_args[0] == "task_failed"
        assert "kaboom" in ev_kwargs["reason"]


class TestWorkflowHandlerIdempotency:
    @pytest.mark.asyncio
    async def test_skips_when_execution_already_completed(self) -> None:
        service = _make_service()
        cms, _bus, mock_exec_task = _patch_workflow_handler(
            existing_execution={
                "status": "completed",
                "outputs": {"cached": True},
                "error": None,
            },
        )
        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            result = await service._workflow_handler(
                data={
                    "workflow_id": "wf5",
                    "inputs": {},
                    "execution_id": "exec-1",
                },
            )
        # Short-circuited: orchestrator never invoked.
        mock_exec_task.assert_not_awaited()
        assert result["skipped"] == "already_terminal"
        assert result["success"] is True
        assert result["output"] == {"cached": True}

    @pytest.mark.asyncio
    async def test_skips_when_execution_already_failed(self) -> None:
        service = _make_service()
        cms, _bus, mock_exec_task = _patch_workflow_handler(
            existing_execution={
                "status": "failed",
                "outputs": {},
                "error": "prior failure",
            },
        )
        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            result = await service._workflow_handler(
                data={
                    "workflow_id": "wf6",
                    "inputs": {},
                    "execution_id": "exec-2",
                },
            )
        mock_exec_task.assert_not_awaited()
        assert result["skipped"] == "already_terminal"
        assert result["success"] is False
        assert result["error"] == "prior failure"

    @pytest.mark.asyncio
    async def test_non_terminal_execution_proceeds(self) -> None:
        service = _make_service()
        cms, _bus, mock_exec_task = _patch_workflow_handler(
            existing_execution={"status": "running"},
            orchestrator_result={"success": True, "outputs": {}},
        )
        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            result = await service._workflow_handler(
                data={
                    "workflow_id": "wf7",
                    "inputs": {},
                    "execution_id": "exec-3",
                },
            )
        mock_exec_task.assert_awaited_once()
        assert result["success"] is True


def _patch_step_handler(
    *,
    result: dict[str, Any] | None = None,
    exc: Exception | None = None,
) -> tuple[Any, AsyncMock]:
    if exc is not None:
        mock_step_task = AsyncMock(side_effect=exc)
    else:
        mock_step_task = AsyncMock(return_value=result or {})
    cm = patch(
        "chaoscypher_core.operations.workflows.orchestrator.execute_step_task",
        mock_step_task,
    )
    return cm, mock_step_task


class TestStepHandler:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        service = _make_service()
        cm, mock_step_task = _patch_step_handler(
            result={"success": True, "output": {"r": 42}, "error": None},
        )
        with cm:
            result = await service._step_handler(
                data={
                    "step_id": "step-1",
                    "step_config": {"type": "ai.prompt"},
                    "step_inputs": {"x": 1},
                    "workflow_context": {"ctx": "y"},
                },
            )
        assert result["success"] is True
        assert result["output"] == {"r": 42}
        _, kwargs = mock_step_task.call_args
        assert kwargs["step_config"] == {"type": "ai.prompt"}
        assert kwargs["workflow_context"] == {"ctx": "y"}
        assert kwargs["database_name"] == "db-test"

    @pytest.mark.asyncio
    async def test_missing_workflow_context_defaults_to_empty(self) -> None:
        service = _make_service()
        cm, mock_step_task = _patch_step_handler(
            result={"success": True, "output": {}},
        )
        with cm:
            await service._step_handler(
                data={
                    "step_id": "step-2",
                    "step_config": {},
                    "step_inputs": {},
                },
            )
        _, kwargs = mock_step_task.call_args
        assert kwargs["workflow_context"] == {}

    @pytest.mark.asyncio
    async def test_exception_returns_error_dict(self) -> None:
        service = _make_service()
        cm, _task = _patch_step_handler(exc=ValueError("step blew up"))
        with cm:
            result = await service._step_handler(
                data={
                    "step_id": "step-3",
                    "step_config": {},
                    "step_inputs": {},
                },
            )
        assert result["success"] is False
        assert result["output"] == {}
        assert "step blew up" in result["error"]
