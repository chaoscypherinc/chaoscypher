# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage tests for WorkflowStepsService (management/step.py).

MagicMock repository collaborator exercises list/get/create/update/delete
and reorder, including the not-found, system-workflow ACL, auto-numbering
and validation branches.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from chaoscypher_core.services.workflows.management.step import WorkflowStepsService


def _make_service(
    workflow: dict[str, Any] | None = None,
) -> tuple[WorkflowStepsService, MagicMock]:
    repo = MagicMock()
    repo.get_workflow.return_value = workflow
    return WorkflowStepsService(repository=repo), repo


def _wf(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "w1", "is_system": False}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# list_steps
# ---------------------------------------------------------------------------


def test_list_steps_workflow_not_found() -> None:
    svc, _ = _make_service(workflow=None)
    with pytest.raises(NotFoundError):
        svc.list_steps("missing")


def test_list_steps_returns_steps() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.get_workflow_steps.return_value = [{"id": "s1"}, {"id": "s2"}]
    assert svc.list_steps("w1") == [{"id": "s1"}, {"id": "s2"}]


# ---------------------------------------------------------------------------
# get_step
# ---------------------------------------------------------------------------


def test_get_step_workflow_not_found() -> None:
    svc, _ = _make_service(workflow=None)
    with pytest.raises(NotFoundError):
        svc.get_step("missing", "s1")


def test_get_step_step_missing() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.get_workflow_step.return_value = None
    with pytest.raises(NotFoundError):
        svc.get_step("w1", "s1")


def test_get_step_step_wrong_workflow() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.get_workflow_step.return_value = {"id": "s1", "workflow_id": "other"}
    with pytest.raises(NotFoundError):
        svc.get_step("w1", "s1")


def test_get_step_success() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.get_workflow_step.return_value = {"id": "s1", "workflow_id": "w1"}
    assert svc.get_step("w1", "s1") == {"id": "s1", "workflow_id": "w1"}


# ---------------------------------------------------------------------------
# create_step
# ---------------------------------------------------------------------------


def test_create_step_workflow_not_found() -> None:
    svc, _ = _make_service(workflow=None)
    with pytest.raises(NotFoundError):
        svc.create_step("missing", {"name": "s"})


def test_create_step_system_workflow_rejected() -> None:
    svc, _ = _make_service(workflow=_wf(is_system=True))
    with pytest.raises(AuthorizationError):
        svc.create_step("w1", {"name": "s"})


def test_create_step_auto_numbers_from_existing_max() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.get_workflow_steps.return_value = [
        {"step_number": 1},
        {"step_number": 4},
    ]
    repo.create_workflow_step.side_effect = lambda d: d
    created = svc.create_step(
        "w1",
        {"name": "new", "tool_type": "system", "tool_id": "t"},
    )
    assert created["step_number"] == 5
    repo.update_workflow.assert_called_once()  # updated_at bump


def test_create_step_auto_numbers_default_when_empty() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.get_workflow_steps.return_value = []
    repo.create_workflow_step.side_effect = lambda d: d
    created = svc.create_step("w1", {"name": "new", "tool_type": "system", "tool_id": "t"})
    assert created["step_number"] == 1


def test_create_step_uses_provided_step_number() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.create_workflow_step.side_effect = lambda d: d
    created = svc.create_step(
        "w1",
        {"name": "new", "tool_type": "system", "tool_id": "t", "step_number": 9},
    )
    assert created["step_number"] == 9
    # No need to list existing steps when number is explicit.
    repo.get_workflow_steps.assert_not_called()


def test_create_step_builds_defaults() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.create_workflow_step.side_effect = lambda d: d
    created = svc.create_step(
        "w1",
        {"name": "new", "tool_type": "system", "tool_id": "t", "step_number": 1},
    )
    assert created["configuration"] == {}
    assert created["depends_on"] == []
    assert created["continue_on_error"] is False
    assert created["workflow_id"] == "w1"


# ---------------------------------------------------------------------------
# update_step
# ---------------------------------------------------------------------------


def test_update_step_workflow_not_found() -> None:
    svc, _ = _make_service(workflow=None)
    with pytest.raises(NotFoundError):
        svc.update_step("missing", "s1", {"name": "x"})


def test_update_step_system_workflow_rejected() -> None:
    svc, _ = _make_service(workflow=_wf(is_system=True))
    with pytest.raises(AuthorizationError):
        svc.update_step("w1", "s1", {"name": "x"})


def test_update_step_step_missing() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.get_workflow_step.return_value = None
    with pytest.raises(NotFoundError):
        svc.update_step("w1", "s1", {"name": "x"})


def test_update_step_wrong_workflow() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.get_workflow_step.return_value = {"id": "s1", "workflow_id": "other"}
    with pytest.raises(NotFoundError):
        svc.update_step("w1", "s1", {"name": "x"})


def test_update_step_filters_allowed_fields() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.get_workflow_step.return_value = {"id": "s1", "workflow_id": "w1"}
    repo.update_workflow_step.side_effect = lambda sid, updates: {"id": sid, **updates}
    out = svc.update_step("w1", "s1", {"name": "new", "not_allowed": 1})
    updates = repo.update_workflow_step.call_args[0][1]
    assert updates["name"] == "new"
    assert "not_allowed" not in updates
    assert "updated_at" in updates
    assert out["name"] == "new"
    repo.update_workflow.assert_called_once()


# ---------------------------------------------------------------------------
# delete_step
# ---------------------------------------------------------------------------


def test_delete_step_workflow_not_found() -> None:
    svc, _ = _make_service(workflow=None)
    with pytest.raises(NotFoundError):
        svc.delete_step("missing", "s1")


def test_delete_step_system_workflow_rejected() -> None:
    svc, _ = _make_service(workflow=_wf(is_system=True))
    with pytest.raises(AuthorizationError):
        svc.delete_step("w1", "s1")


def test_delete_step_step_missing() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.get_workflow_step.return_value = None
    with pytest.raises(NotFoundError):
        svc.delete_step("w1", "s1")


def test_delete_step_success() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.get_workflow_step.return_value = {"id": "s1", "workflow_id": "w1"}
    svc.delete_step("w1", "s1")
    repo.delete_workflow_step.assert_called_once_with("s1")
    repo.update_workflow.assert_called_once()


# ---------------------------------------------------------------------------
# reorder_steps
# ---------------------------------------------------------------------------


def test_reorder_steps_workflow_not_found() -> None:
    svc, _ = _make_service(workflow=None)
    with pytest.raises(NotFoundError):
        svc.reorder_steps("missing", ["s1"])


def test_reorder_steps_system_workflow_rejected() -> None:
    svc, _ = _make_service(workflow=_wf(is_system=True))
    with pytest.raises(AuthorizationError):
        svc.reorder_steps("w1", ["s1"])


def test_reorder_steps_invalid_order_missing_and_extra() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.get_workflow_steps.return_value = [{"id": "s1"}, {"id": "s2"}]
    # Provides s1 + unknown s9; omits s2 -> both missing and extra reported.
    with pytest.raises(ValidationError):
        svc.reorder_steps("w1", ["s1", "s9"])


def test_reorder_steps_success_renumbers() -> None:
    svc, repo = _make_service(workflow=_wf())
    repo.get_workflow_steps.return_value = [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]
    repo.update_workflow_step.side_effect = lambda sid, updates: {"id": sid, **updates}
    result = svc.reorder_steps("w1", ["s3", "s1", "s2"])
    # New step numbers assigned 1..N in given order.
    assert result[0]["id"] == "s3"
    assert result[0]["step_number"] == 1
    assert result[1]["id"] == "s1"
    assert result[1]["step_number"] == 2
    assert result[2]["step_number"] == 3
    repo.update_workflow.assert_called_once()
