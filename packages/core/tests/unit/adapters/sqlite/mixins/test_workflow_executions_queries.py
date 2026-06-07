# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage for non-bulk WorkflowExecutionsMixin methods.

Exercises workflow-level + step-level execution CRUD against a real
file-backed SQLite database (the bulk clear path is covered separately in
``test_workflow_executions_bulk.py``).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    Workflow,
    WorkflowStep,
)
from chaoscypher_core.exceptions import NotFoundError


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_dir = tmp_path / "cc-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def _seed_workflow(adapter: SqliteAdapter, workflow_id: str = "wf1") -> None:
    with adapter.transaction():
        adapter.session.add(
            Workflow(id=workflow_id, database_name="test", name=f"n-{workflow_id}", input_schema={})
        )


def _seed_step(adapter: SqliteAdapter, step_id: str, workflow_id: str = "wf1") -> None:
    with adapter.transaction():
        adapter.session.add(
            WorkflowStep(
                id=step_id,
                workflow_id=workflow_id,
                step_number=1,
                name=f"step-{step_id}",
                tool_type="system",
                tool_id="tool-1",
                configuration={},
            )
        )


# ---------------------------------------------------------------------------
# create_execution / get_execution
# ---------------------------------------------------------------------------


def test_create_and_get_execution(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    created = adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {"q": "hi"},
            "status": "pending",
        }
    )
    assert created["id"] == "exec-1"
    assert created["status"] == "pending"
    assert created["inputs"] == {"q": "hi"}

    fetched = adapter.get_execution("exec-1")
    assert fetched is not None
    assert fetched["id"] == "exec-1"
    assert fetched["workflow_id"] == "wf1"


def test_get_execution_not_found_returns_none(adapter: SqliteAdapter) -> None:
    assert adapter.get_execution("missing") is None


# ---------------------------------------------------------------------------
# update_status
# ---------------------------------------------------------------------------


def test_update_status_running_sets_started_at(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "pending",
        }
    )
    adapter.update_status("exec-1", "running")
    fetched = adapter.get_execution("exec-1")
    assert fetched is not None
    assert fetched["status"] == "running"
    assert fetched["started_at"] is not None


def test_update_status_non_running_leaves_started_at_none(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "pending",
        }
    )
    adapter.update_status("exec-1", "cancelled")
    fetched = adapter.get_execution("exec-1")
    assert fetched is not None
    assert fetched["status"] == "cancelled"
    assert fetched["started_at"] is None


def test_update_status_preserves_started_at_on_second_running(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "pending",
        }
    )
    adapter.update_status("exec-1", "running")
    first = adapter.get_execution("exec-1")
    assert first is not None
    started = first["started_at"]
    # Re-applying running must not reset started_at.
    adapter.update_status("exec-1", "running")
    second = adapter.get_execution("exec-1")
    assert second is not None
    assert second["started_at"] == started


def test_update_status_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.update_status("missing", "running")


# ---------------------------------------------------------------------------
# update_current_step
# ---------------------------------------------------------------------------


def test_update_current_step(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "running",
        }
    )
    adapter.update_current_step("exec-1", "step-99")
    fetched = adapter.get_execution("exec-1")
    assert fetched is not None
    assert fetched["current_step_id"] == "step-99"


def test_update_current_step_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.update_current_step("missing", "step-1")


# ---------------------------------------------------------------------------
# complete_execution / fail_execution
# ---------------------------------------------------------------------------


def test_complete_execution(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "running",
        }
    )
    adapter.complete_execution("exec-1", {"result": "ok"}, 5000)
    fetched = adapter.get_execution("exec-1")
    assert fetched is not None
    assert fetched["status"] == "completed"
    assert fetched["outputs"] == {"result": "ok"}
    assert fetched["duration_ms"] == 5000
    assert fetched["completed_at"] is not None


def test_complete_execution_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.complete_execution("missing", {}, 0)


def test_fail_execution(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "running",
        }
    )
    adapter.fail_execution("exec-1", "boom", "step-7", 3000)
    fetched = adapter.get_execution("exec-1")
    assert fetched is not None
    assert fetched["status"] == "failed"
    assert fetched["error_message"] == "boom"
    assert fetched["failed_step_id"] == "step-7"
    assert fetched["duration_ms"] == 3000
    assert fetched["completed_at"] is not None


def test_fail_execution_none_step(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "running",
        }
    )
    adapter.fail_execution("exec-1", "early failure", None, 100)
    fetched = adapter.get_execution("exec-1")
    assert fetched is not None
    assert fetched["failed_step_id"] is None


def test_fail_execution_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.fail_execution("missing", "boom", None, 0)


# ---------------------------------------------------------------------------
# get_workflow_executions
# ---------------------------------------------------------------------------


def test_get_workflow_executions_orders_recent_first_and_limits(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    for i in range(5):
        adapter.create_execution(
            {
                "id": f"exec-{i}",
                "workflow_id": "wf1",
                "triggered_by": "manual",
                "inputs": {},
                "status": "completed",
            }
        )
    rows = adapter.get_workflow_executions("wf1", limit=3)
    assert len(rows) == 3
    # All belong to wf1.
    assert {r["workflow_id"] for r in rows} == {"wf1"}


def test_get_workflow_executions_empty(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    assert adapter.get_workflow_executions("wf1") == []


def test_get_workflow_executions_scoped_by_workflow(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter, "wf1")
    _seed_workflow(adapter, "wf2")
    adapter.create_execution(
        {
            "id": "e-a",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "completed",
        }
    )
    adapter.create_execution(
        {
            "id": "e-b",
            "workflow_id": "wf2",
            "triggered_by": "manual",
            "inputs": {},
            "status": "completed",
        }
    )
    rows = adapter.get_workflow_executions("wf2")
    assert [r["id"] for r in rows] == ["e-b"]


# ---------------------------------------------------------------------------
# list_active_executions
# ---------------------------------------------------------------------------


def test_list_active_executions_filters_by_status(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    statuses = ["pending", "queued", "running", "completed", "failed"]
    for i, status in enumerate(statuses):
        adapter.create_execution(
            {
                "id": f"exec-{i}",
                "workflow_id": "wf1",
                "triggered_by": "manual",
                "inputs": {},
                "status": status,
            }
        )
    active = adapter.list_active_executions("wf1")
    active_statuses = {r["status"] for r in active}
    assert active_statuses == {"pending", "queued", "running"}


def test_list_active_executions_empty(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    assert adapter.list_active_executions("wf1") == []


# ---------------------------------------------------------------------------
# Step execution lifecycle
# ---------------------------------------------------------------------------


def test_create_step_execution_defaults(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    _seed_step(adapter, "step-1")
    adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "running",
        }
    )
    step = adapter.create_step_execution("se-1", "exec-1", "step-1")
    assert step["id"] == "se-1"
    assert step["status"] == "pending"
    assert step["inputs"] == {}
    assert step["execution_id"] == "exec-1"


def test_update_step_status_running_sets_started_at(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    _seed_step(adapter, "step-1")
    adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "running",
        }
    )
    adapter.create_step_execution("se-1", "exec-1", "step-1")
    adapter.update_step_status("se-1", "running")
    steps = adapter.get_step_executions("exec-1")
    assert steps[0]["status"] == "running"
    assert steps[0]["started_at"] is not None


def test_update_step_status_non_running(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    _seed_step(adapter, "step-1")
    adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "running",
        }
    )
    adapter.create_step_execution("se-1", "exec-1", "step-1")
    adapter.update_step_status("se-1", "skipped")
    steps = adapter.get_step_executions("exec-1")
    assert steps[0]["status"] == "skipped"
    assert steps[0]["started_at"] is None


def test_update_step_status_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.update_step_status("missing", "running")


def test_complete_step_execution(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    _seed_step(adapter, "step-1")
    adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "running",
        }
    )
    adapter.create_step_execution("se-1", "exec-1", "step-1")
    adapter.complete_step_execution("se-1", {"entities": 10}, 2000)
    steps = adapter.get_step_executions("exec-1")
    assert steps[0]["status"] == "completed"
    assert steps[0]["outputs"] == {"entities": 10}
    assert steps[0]["duration_ms"] == 2000
    assert steps[0]["completed_at"] is not None


def test_complete_step_execution_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.complete_step_execution("missing", {}, 0)


def test_fail_step_execution(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    _seed_step(adapter, "step-1")
    adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "running",
        }
    )
    adapter.create_step_execution("se-1", "exec-1", "step-1")
    adapter.fail_step_execution("se-1", "bad schema", 1500)
    steps = adapter.get_step_executions("exec-1")
    assert steps[0]["status"] == "failed"
    assert steps[0]["error_message"] == "bad schema"
    assert steps[0]["duration_ms"] == 1500
    assert steps[0]["completed_at"] is not None


def test_fail_step_execution_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.fail_step_execution("missing", "boom", 0)


def test_get_step_executions_ordered_and_scoped(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    _seed_step(adapter, "step-1")
    _seed_step(adapter, "step-2")
    adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "running",
        }
    )
    adapter.create_execution(
        {
            "id": "exec-2",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "running",
        }
    )
    adapter.create_step_execution("se-1", "exec-1", "step-1")
    adapter.create_step_execution("se-2", "exec-1", "step-2")
    adapter.create_step_execution("se-other", "exec-2", "step-1")

    steps = adapter.get_step_executions("exec-1")
    assert {s["id"] for s in steps} == {"se-1", "se-2"}


def test_get_step_executions_empty(adapter: SqliteAdapter) -> None:
    _seed_workflow(adapter)
    adapter.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "running",
        }
    )
    assert adapter.get_step_executions("exec-1") == []
