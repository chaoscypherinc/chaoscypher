# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage tests for WorkflowService (management/service.py).

MagicMock storage collaborator exercises the CRUD, steps-CRUD, statistics
and global-stats logic. Export/import are delegated to
WorkflowPortabilityService and are only asserted to forward arguments.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from chaoscypher_core.services.workflows.management.service import WorkflowService


def _make_service(storage: MagicMock | None = None) -> tuple[WorkflowService, MagicMock]:
    storage = storage or MagicMock()
    svc = WorkflowService(storage=storage, database_name="db1")
    return svc, storage


# ---------------------------------------------------------------------------
# Workflow CRUD
# ---------------------------------------------------------------------------


def test_list_workflows_forwards_filters() -> None:
    svc, storage = _make_service()
    storage.list_workflows.return_value = [{"id": "w1"}]
    out = svc.list_workflows(category="research", is_system=False, is_active=True)
    assert out == [{"id": "w1"}]
    storage.list_workflows.assert_called_once_with(
        database_name="db1",
        category="research",
        is_system=False,
        is_active=True,
        expose_as_ai_tool=None,
    )


def test_get_workflow_delegates() -> None:
    svc, storage = _make_service()
    storage.get_workflow.return_value = {"id": "w1"}
    assert svc.get_workflow("w1") == {"id": "w1"}


def test_list_workflows_by_ids_delegates() -> None:
    svc, storage = _make_service()
    storage.list_workflows_by_ids.return_value = [{"id": "w1"}, {"id": "w2"}]
    assert svc.list_workflows_by_ids(["w1", "w2"]) == [{"id": "w1"}, {"id": "w2"}]


def test_create_workflow_builds_dict_and_stats() -> None:
    svc, storage = _make_service()
    storage.create_workflow.return_value = {"id": "w1", "name": "My WF", "category": "x"}
    wid = svc.create_workflow(
        {"name": "My WF", "input_schema": {"type": "object"}, "category": "x"}
    )
    assert wid == "w1"
    created = storage.create_workflow.call_args[0][0]
    assert created["database_name"] == "db1"
    assert created["name"] == "My WF"
    assert created["is_active"] is True
    assert created["version"] == "1.0.0"
    storage.create_workflow_statistics.assert_called_once()


def test_create_workflow_honors_explicit_id() -> None:
    svc, storage = _make_service()
    storage.create_workflow.return_value = {"id": "custom", "name": "n"}
    svc.create_workflow({"id": "custom", "name": "n", "input_schema": {}})
    assert storage.create_workflow.call_args[0][0]["id"] == "custom"


def test_update_workflow_not_found_returns_false() -> None:
    svc, storage = _make_service()
    storage.get_workflow.return_value = None
    assert svc.update_workflow("missing", {"name": "x"}) is False
    storage.update_workflow.assert_not_called()


def test_update_workflow_filters_allowed_fields() -> None:
    svc, storage = _make_service()
    storage.get_workflow.return_value = {"id": "w1", "name": "old"}
    ok = svc.update_workflow(
        "w1", {"name": "new", "id": "hacker", "created_at": "nope", "icon": "star"}
    )
    assert ok is True
    update_arg = storage.update_workflow.call_args[0][1]
    assert update_arg["name"] == "new"
    assert update_arg["icon"] == "star"
    assert "id" not in update_arg  # not in allowed_fields
    assert "created_at" not in update_arg  # not in allowed_fields, never copied
    assert "updated_at" in update_arg  # injected by the service


def test_update_workflow_does_not_copy_unknown_fields() -> None:
    svc, storage = _make_service()
    storage.get_workflow.return_value = {"id": "w1"}
    svc.update_workflow("w1", {"bogus": 1})
    update_arg = storage.update_workflow.call_args[0][1]
    assert "bogus" not in update_arg
    assert "updated_at" in update_arg


def test_delete_workflow_not_found_returns_false() -> None:
    svc, storage = _make_service()
    storage.get_workflow.return_value = None
    assert svc.delete_workflow("missing") is False
    storage.delete_workflow.assert_not_called()


def test_delete_workflow_success() -> None:
    svc, storage = _make_service()
    storage.get_workflow.return_value = {"id": "w1", "name": "n"}
    assert svc.delete_workflow("w1") is True
    storage.delete_workflow.assert_called_once_with("w1")


# ---------------------------------------------------------------------------
# Workflow steps CRUD
# ---------------------------------------------------------------------------


def test_list_workflow_steps_delegates() -> None:
    svc, storage = _make_service()
    storage.get_workflow_steps.return_value = [{"id": "s1"}]
    assert svc.list_workflow_steps("w1") == [{"id": "s1"}]


def test_get_workflow_step_delegates() -> None:
    svc, storage = _make_service()
    storage.get_workflow_step.return_value = {"id": "s1"}
    assert svc.get_workflow_step("s1") == {"id": "s1"}


def test_create_workflow_step_builds_dict() -> None:
    svc, storage = _make_service()
    storage.create_workflow_step.return_value = {
        "id": "s1",
        "name": "step",
        "workflow_id": "w1",
        "step_number": 1,
        "tool_type": "system",
        "tool_id": "t",
    }
    sid = svc.create_workflow_step(
        {
            "workflow_id": "w1",
            "step_number": 1,
            "name": "step",
            "tool_type": "system",
            "tool_id": "t",
            "configuration": {"a": 1},
        }
    )
    assert sid == "s1"
    built = storage.create_workflow_step.call_args[0][0]
    assert built["workflow_id"] == "w1"
    assert built["retry_on_failure"] is False
    assert built["depends_on"] == []


def test_update_workflow_step_not_found() -> None:
    svc, storage = _make_service()
    storage.get_workflow_step.return_value = None
    assert svc.update_workflow_step("missing", {"name": "x"}) is False
    storage.update_workflow_step.assert_not_called()


def test_update_workflow_step_filters_fields() -> None:
    svc, storage = _make_service()
    storage.get_workflow_step.return_value = {"id": "s1", "name": "old", "workflow_id": "w1"}
    ok = svc.update_workflow_step("s1", {"name": "new", "not_allowed": 5})
    assert ok is True
    filtered = storage.update_workflow_step.call_args[0][1]
    assert filtered["name"] == "new"
    assert "not_allowed" not in filtered
    assert "updated_at" in filtered


def test_delete_workflow_step_not_found() -> None:
    svc, storage = _make_service()
    storage.get_workflow_step.return_value = None
    assert svc.delete_workflow_step("missing") is False
    storage.delete_workflow_step.assert_not_called()


def test_delete_workflow_step_success() -> None:
    svc, storage = _make_service()
    storage.get_workflow_step.return_value = {"id": "s1", "name": "n", "workflow_id": "w1"}
    assert svc.delete_workflow_step("s1") is True
    storage.delete_workflow_step.assert_called_once_with("s1")


def test_delete_workflow_steps_returns_count() -> None:
    svc, storage = _make_service()
    storage.delete_workflow_steps.return_value = 3
    assert svc.delete_workflow_steps("w1") == 3


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def test_get_workflow_statistics_delegates() -> None:
    svc, storage = _make_service()
    storage.get_workflow_statistics.return_value = {"workflow_id": "w1"}
    assert svc.get_workflow_statistics("w1") == {"workflow_id": "w1"}


def test_update_workflow_statistics_creates_when_missing() -> None:
    svc, storage = _make_service()
    storage.get_workflow_statistics.return_value = None
    storage.create_workflow_statistics.return_value = {
        "workflow_id": "w1",
        "total_executions": 0,
        "successful_executions": 0,
        "failed_executions": 0,
        "cancelled_executions": 0,
        "avg_duration_ms": 0,
        "min_duration_ms": None,
        "max_duration_ms": None,
    }
    svc.update_workflow_statistics("w1", duration_ms=500, status="success")
    storage.create_workflow_statistics.assert_called_once()
    updates = storage.update_workflow_statistics.call_args[0][1]
    assert updates["total_executions"] == 1
    assert updates["successful_executions"] == 1
    assert updates["last_success_at"] is not None
    assert updates["min_duration_ms"] == 500
    assert updates["max_duration_ms"] == 500
    assert updates["avg_duration_ms"] == 500


def test_update_workflow_statistics_failed_branch() -> None:
    svc, storage = _make_service()
    storage.get_workflow_statistics.return_value = {
        "total_executions": 2,
        "successful_executions": 2,
        "failed_executions": 0,
        "cancelled_executions": 0,
        "avg_duration_ms": 100,
        "min_duration_ms": 50,
        "max_duration_ms": 150,
    }
    svc.update_workflow_statistics("w1", duration_ms=200, status="failed")
    updates = storage.update_workflow_statistics.call_args[0][1]
    assert updates["failed_executions"] == 1
    assert "last_failure_at" in updates
    # New duration 200 is a new max, min unchanged (not updated since 200 > 50)
    assert updates["max_duration_ms"] == 200
    assert "min_duration_ms" not in updates
    # avg = (100*2 + 200)/3 = 133
    assert updates["avg_duration_ms"] == 133


def test_update_workflow_statistics_cancelled_branch() -> None:
    svc, storage = _make_service()
    storage.get_workflow_statistics.return_value = {
        "total_executions": 1,
        "cancelled_executions": 0,
        "avg_duration_ms": 0,
        "min_duration_ms": None,
        "max_duration_ms": None,
    }
    svc.update_workflow_statistics("w1", duration_ms=10, status="cancelled")
    updates = storage.update_workflow_statistics.call_args[0][1]
    assert updates["cancelled_executions"] == 1
    assert "last_success_at" not in updates
    assert "last_failure_at" not in updates


# ---------------------------------------------------------------------------
# Export / import (delegated)
# ---------------------------------------------------------------------------


def test_export_workflow_delegates_to_portability() -> None:
    svc, _ = _make_service()
    svc.portability_service = MagicMock()
    svc.portability_service.export_workflow.return_value = {"format": "x"}
    assert svc.export_workflow("w1") == {"format": "x"}
    svc.portability_service.export_workflow.assert_called_once_with("w1")


def test_import_workflow_forwards_all_args() -> None:
    svc, _ = _make_service()
    svc.portability_service = MagicMock()
    svc.portability_service.import_workflow.return_value = {"workflow_id": "w1"}
    data: dict[str, Any] = {"workflow": {}}
    out = svc.import_workflow(data, on_duplicate="rename", new_name="N", import_as_inactive=True)
    assert out == {"workflow_id": "w1"}
    svc.portability_service.import_workflow.assert_called_once_with(
        workflow_data=data,
        on_duplicate="rename",
        new_name="N",
        import_as_inactive=True,
    )


# ---------------------------------------------------------------------------
# Global stats
# ---------------------------------------------------------------------------


def test_get_global_stats_aggregates() -> None:
    svc, storage = _make_service()
    storage.list_workflows.return_value = [
        {"id": "w1", "is_active": True},
        {"id": "w2", "is_active": False},
    ]

    def stats_for(wid: str) -> dict[str, Any] | None:
        if wid == "w1":
            return {
                "total_executions": 4,
                "successful_executions": 3,
                "failed_executions": 1,
                "cancelled_executions": 0,
            }
        return None  # w2 has no stats row

    storage.get_workflow_statistics.side_effect = stats_for
    result = svc.get_global_stats()
    assert result["total_workflows"] == 2
    assert result["active_workflows"] == 1
    assert result["inactive_workflows"] == 1
    assert result["total_executions"] == 4
    assert result["successful_executions"] == 3
    assert result["failed_executions"] == 1
    assert result["success_rate"] == 75.0


def test_get_global_stats_no_executions_zero_rate() -> None:
    svc, storage = _make_service()
    storage.list_workflows.return_value = [{"id": "w1", "is_active": True}]
    storage.get_workflow_statistics.return_value = None
    result = svc.get_global_stats()
    assert result["success_rate"] == 0.0
    assert result["total_executions"] == 0
