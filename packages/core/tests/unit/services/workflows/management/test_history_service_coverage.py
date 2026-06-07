# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage tests for WorkflowExecutionService (management/history.py).

Uses MagicMock repositories / operations-service collaborators so the
service's branching logic (access control, status filtering, stats math,
enqueue-failure handling, cancellation) is exercised in isolation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import (
    AuthorizationError,
    NotFoundError,
    OperationError,
    ValidationError,
)
from chaoscypher_core.services.workflows.management.history import (
    WorkflowExecutionService,
    _normalize_user,
)


def _make_service(
    *,
    workflow: dict[str, Any] | None = None,
    stats_max_executions: int = 1000,
) -> tuple[WorkflowExecutionService, MagicMock, MagicMock, MagicMock]:
    """Build a service with MagicMock collaborators.

    Returns (service, repository, execution_repo, ops_service).
    """
    repo = MagicMock()
    repo.get_workflow.return_value = workflow
    exec_repo = MagicMock()
    ops = MagicMock()
    ops.enqueue_operation = AsyncMock(return_value="task")
    ops.abort_operation = AsyncMock(return_value=None)
    svc = WorkflowExecutionService(
        repository=repo,
        execution_repository=exec_repo,
        operations_service=ops,
        stats_max_executions=stats_max_executions,
    )
    return svc, repo, exec_repo, ops


def _active_workflow(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "w1",
        "name": "wf",
        "is_active": True,
        "is_system": False,
        "user_id": None,
        "allow_parallel_execution": True,
        "expose_as_ai_tool": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _normalize_user helper
# ---------------------------------------------------------------------------


def test_normalize_user_none_returns_none() -> None:
    assert _normalize_user(None) is None


def test_normalize_user_dict() -> None:
    p = _normalize_user({"id": 5, "is_admin": True})
    assert p is not None
    assert p.id == 5
    assert p.is_admin is True


def test_normalize_user_object_attributes() -> None:
    class U:
        id = 9
        is_admin = False

    p = _normalize_user(U())
    assert p is not None
    assert p.id == 9
    assert p.is_admin is False


def test_normalize_user_invalid_type_raises() -> None:
    with pytest.raises(TypeError):
        _normalize_user(12345)


# ---------------------------------------------------------------------------
# execute_workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_workflow_not_found() -> None:
    svc, _, _, _ = _make_service(workflow=None)
    with pytest.raises(NotFoundError):
        await svc.execute_workflow("missing", {})


@pytest.mark.asyncio
async def test_execute_workflow_access_denied_for_other_owner() -> None:
    svc, _, _, _ = _make_service(workflow=_active_workflow(user_id=1))
    with pytest.raises(AuthorizationError):
        await svc.execute_workflow("w1", {}, user={"id": 2, "is_admin": False})


@pytest.mark.asyncio
async def test_execute_workflow_inactive_raises_validation() -> None:
    svc, _, _, _ = _make_service(workflow=_active_workflow(is_active=False))
    with pytest.raises(ValidationError):
        await svc.execute_workflow("w1", {})


@pytest.mark.asyncio
async def test_execute_workflow_happy_path_enqueues() -> None:
    svc, _, exec_repo, ops = _make_service(workflow=_active_workflow())
    eid = await svc.execute_workflow("w1", {"k": "v"}, triggered_by="manual")
    assert isinstance(eid, str)
    # Execution record created before queueing
    exec_repo.create_execution.assert_called_once()
    created = exec_repo.create_execution.call_args[0][0]
    assert created["status"] == "pending"
    assert created["workflow_id"] == "w1"
    # Enqueued via operations service
    ops.enqueue_operation.assert_awaited_once()
    assert ops.enqueue_operation.await_args.kwargs["operation_type"] == "execute_workflow"


@pytest.mark.asyncio
async def test_execute_workflow_owner_admin_bypasses_acl() -> None:
    svc, _, _, ops = _make_service(workflow=_active_workflow(user_id=1))
    await svc.execute_workflow("w1", {}, user={"id": 99, "is_admin": True})
    ops.enqueue_operation.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_workflow_enqueue_failure_marks_failed_and_raises(
    structlog_for_caplog: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    svc, _, exec_repo, ops = _make_service(workflow=_active_workflow())
    ops.enqueue_operation = AsyncMock(side_effect=RuntimeError("queue down"))
    with pytest.raises(OperationError):
        await svc.execute_workflow("w1", {})
    exec_repo.fail_execution.assert_called_once()
    assert "workflow_execution_enqueue_failed" in caplog.text


# ---------------------------------------------------------------------------
# get_executions
# ---------------------------------------------------------------------------


def test_get_executions_not_found() -> None:
    svc, _, _, _ = _make_service(workflow=None)
    with pytest.raises(NotFoundError):
        svc.get_executions("missing")


def test_get_executions_access_denied() -> None:
    svc, _, _, _ = _make_service(workflow=_active_workflow(user_id=1))
    with pytest.raises(AuthorizationError):
        svc.get_executions("w1", user={"id": 2, "is_admin": False})


def test_get_executions_status_filter_and_pagination() -> None:
    svc, _, exec_repo, _ = _make_service(workflow=_active_workflow())
    exec_repo.get_workflow_executions.return_value = [
        {"id": "a", "status": "completed"},
        {"id": "b", "status": "failed"},
        {"id": "c", "status": "completed"},
        {"id": "d", "status": "completed"},
    ]
    result = svc.get_executions("w1", limit=2, skip=1, status_filter="completed")
    # Filter keeps a, c, d; skip=1 -> [c, d]; limit=2 -> [c, d]
    assert [r["id"] for r in result] == ["c", "d"]


def test_get_executions_no_filter() -> None:
    svc, _, exec_repo, _ = _make_service(workflow=_active_workflow())
    exec_repo.get_workflow_executions.return_value = [{"id": "a"}, {"id": "b"}]
    assert len(svc.get_executions("w1", limit=10)) == 2


# ---------------------------------------------------------------------------
# get_execution
# ---------------------------------------------------------------------------


def test_get_execution_workflow_not_found() -> None:
    svc, _, _, _ = _make_service(workflow=None)
    with pytest.raises(NotFoundError):
        svc.get_execution("missing", "e1")


def test_get_execution_not_found() -> None:
    svc, _, exec_repo, _ = _make_service(workflow=_active_workflow())
    exec_repo.get_execution.return_value = None
    with pytest.raises(NotFoundError):
        svc.get_execution("w1", "e1")


def test_get_execution_belongs_to_other_workflow() -> None:
    svc, _, exec_repo, _ = _make_service(workflow=_active_workflow())
    exec_repo.get_execution.return_value = {"id": "e1", "workflow_id": "other"}
    with pytest.raises(NotFoundError):
        svc.get_execution("w1", "e1")


def test_get_execution_attaches_steps() -> None:
    svc, _, exec_repo, _ = _make_service(workflow=_active_workflow())
    exec_repo.get_execution.return_value = {"id": "e1", "workflow_id": "w1"}
    exec_repo.get_step_executions.return_value = [{"id": "s1"}]
    result = svc.get_execution("w1", "e1")
    assert result["step_executions"] == [{"id": "s1"}]


def test_get_execution_steps_none_becomes_empty_list() -> None:
    svc, _, exec_repo, _ = _make_service(workflow=_active_workflow())
    exec_repo.get_execution.return_value = {"id": "e1", "workflow_id": "w1"}
    exec_repo.get_step_executions.return_value = None
    result = svc.get_execution("w1", "e1")
    assert result["step_executions"] == []


# ---------------------------------------------------------------------------
# cancel_execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_execution_workflow_not_found() -> None:
    svc, _, _, _ = _make_service(workflow=None)
    with pytest.raises(NotFoundError):
        await svc.cancel_execution("missing", "e1")


@pytest.mark.asyncio
async def test_cancel_execution_not_found() -> None:
    svc, _, exec_repo, _ = _make_service(workflow=_active_workflow())
    exec_repo.get_execution.return_value = None
    with pytest.raises(NotFoundError):
        await svc.cancel_execution("w1", "e1")


@pytest.mark.asyncio
async def test_cancel_execution_wrong_workflow() -> None:
    svc, _, exec_repo, _ = _make_service(workflow=_active_workflow())
    exec_repo.get_execution.return_value = {"id": "e1", "workflow_id": "other"}
    with pytest.raises(NotFoundError):
        await svc.cancel_execution("w1", "e1")


@pytest.mark.asyncio
async def test_cancel_execution_already_completed() -> None:
    svc, _, exec_repo, _ = _make_service(workflow=_active_workflow())
    exec_repo.get_execution.return_value = {
        "id": "e1",
        "workflow_id": "w1",
        "status": "completed",
    }
    with pytest.raises(ValidationError):
        await svc.cancel_execution("w1", "e1")


@pytest.mark.asyncio
async def test_cancel_execution_success() -> None:
    svc, _, exec_repo, ops = _make_service(workflow=_active_workflow())
    exec_repo.get_execution.return_value = {
        "id": "e1",
        "workflow_id": "w1",
        "status": "running",
    }
    result = await svc.cancel_execution("w1", "e1")
    assert result["status"] == "cancelled"
    ops.abort_operation.assert_awaited_once_with("e1")
    exec_repo.update_status.assert_called_once_with("e1", "cancelled")


@pytest.mark.asyncio
async def test_cancel_execution_swallows_abort_error(
    structlog_for_caplog: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    svc, _, exec_repo, ops = _make_service(workflow=_active_workflow())
    ops.abort_operation = AsyncMock(side_effect=RuntimeError("boom"))
    exec_repo.get_execution.return_value = {
        "id": "e1",
        "workflow_id": "w1",
        "status": "running",
    }
    # Abort failure is logged but cancellation still proceeds.
    result = await svc.cancel_execution("w1", "e1")
    assert result["success"] is True
    exec_repo.update_status.assert_called_once_with("e1", "cancelled")
    assert "workflow_cancel_queue_abort_failed" in caplog.text


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


def test_get_stats_workflow_not_found() -> None:
    svc, _, _, _ = _make_service(workflow=None)
    with pytest.raises(NotFoundError):
        svc.get_stats("missing")


def test_get_stats_access_denied() -> None:
    svc, _, _, _ = _make_service(workflow=_active_workflow(user_id=1))
    with pytest.raises(AuthorizationError):
        svc.get_stats("w1", user={"id": 2, "is_admin": False})


def test_get_stats_empty_executions() -> None:
    svc, _, exec_repo, _ = _make_service(workflow=_active_workflow())
    exec_repo.get_workflow_executions.return_value = []
    stats = svc.get_stats("w1")
    assert stats["total_executions"] == 0
    assert stats["success_rate"] == 0.0
    assert stats["avg_duration_ms"] == 0
    assert stats["min_duration_ms"] is None
    assert stats["max_duration_ms"] is None
    assert stats["last_execution_at"] is None


def test_get_stats_mixed_executions_with_durations() -> None:
    svc, _, exec_repo, _ = _make_service(workflow=_active_workflow(name="My WF"))
    exec_repo.get_workflow_executions.return_value = [
        {"status": "completed", "duration_ms": 100, "created_at": "t5"},
        {"status": "failed", "created_at": "t4"},
        {"status": "completed", "duration_ms": 300, "created_at": "t3"},
        {"status": "cancelled", "created_at": "t2"},
        {"status": "running", "created_at": "t1"},
    ]
    stats = svc.get_stats("w1")
    assert stats["workflow_name"] == "My WF"
    assert stats["total_executions"] == 5
    assert stats["successful_executions"] == 2
    assert stats["failed_executions"] == 1
    assert stats["cancelled_executions"] == 1
    assert stats["running_executions"] == 1
    assert stats["avg_duration_ms"] == 200
    assert stats["min_duration_ms"] == 100
    assert stats["max_duration_ms"] == 300
    assert stats["success_rate"] == 40.0
    # last_execution_at is first row; first completed/failed found in order
    assert stats["last_execution_at"] == "t5"
    assert stats["last_success_at"] == "t5"
    assert stats["last_failure_at"] == "t4"
    assert "updated_at" in stats


def test_get_stats_uses_configured_limit() -> None:
    svc, _, exec_repo, _ = _make_service(workflow=_active_workflow(), stats_max_executions=42)
    exec_repo.get_workflow_executions.return_value = []
    svc.get_stats("w1")
    exec_repo.get_workflow_executions.assert_called_once_with("w1", limit=42)
