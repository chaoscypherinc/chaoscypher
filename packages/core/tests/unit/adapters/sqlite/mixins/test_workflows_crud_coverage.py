# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage tests for the non-bulk WorkflowsMixin methods.

Exercises the connected-adapter CRUD path for workflows, steps and
statistics (the bulk/reset helpers are already covered by
test_workflows_bulk.py). Uses a per-test, file-backed SqliteAdapter.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.exceptions import NotFoundError


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_dir = tmp_path / "cc-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


def _wf_data(wf_id: str = "w1", db: str = "default", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": wf_id,
        "database_name": db,
        "name": f"name-{wf_id}",
        "input_schema": {"type": "object"},
    }
    base.update(overrides)
    return base


def _step_data(step_id: str, wf_id: str, num: int, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": step_id,
        "workflow_id": wf_id,
        "step_number": num,
        "name": f"step-{step_id}",
        "tool_type": "system",
        "tool_id": "tool",
        "configuration": {"a": 1},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Workflow CRUD
# ---------------------------------------------------------------------------


def test_get_workflow_missing_returns_none(adapter: SqliteAdapter) -> None:
    assert adapter.get_workflow("nope") is None


def test_create_and_get_workflow(adapter: SqliteAdapter) -> None:
    created = adapter.create_workflow(_wf_data(category="research"))
    assert created["id"] == "w1"
    fetched = adapter.get_workflow("w1")
    assert fetched is not None
    assert fetched["category"] == "research"


def test_update_workflow(adapter: SqliteAdapter) -> None:
    adapter.create_workflow(_wf_data())
    updated = adapter.update_workflow("w1", {"name": "renamed", "is_active": False})
    assert updated["name"] == "renamed"
    assert updated["is_active"] is False
    assert updated["updated_at"] is not None


def test_update_workflow_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.update_workflow("missing", {"name": "x"})


def test_delete_workflow(adapter: SqliteAdapter) -> None:
    adapter.create_workflow(_wf_data())
    assert adapter.delete_workflow("w1") is True
    assert adapter.get_workflow("w1") is None


def test_delete_workflow_missing_returns_false(adapter: SqliteAdapter) -> None:
    assert adapter.delete_workflow("missing") is False


# ---------------------------------------------------------------------------
# list_workflows / list_workflows_by_ids
# ---------------------------------------------------------------------------


def test_list_workflows_scoped_by_database(adapter: SqliteAdapter) -> None:
    adapter.create_workflow(_wf_data("w1", "default"))
    adapter.create_workflow(_wf_data("w2", "default"))
    adapter.create_workflow(_wf_data("w3", "other"))
    rows = adapter.list_workflows(database_name="default")
    assert {r["id"] for r in rows} == {"w1", "w2"}


def test_list_workflows_all_filters(adapter: SqliteAdapter) -> None:
    adapter.create_workflow(
        _wf_data(
            "w1",
            category="cat",
            is_system=True,
            is_active=True,
            expose_as_ai_tool=True,
        )
    )
    adapter.create_workflow(
        _wf_data(
            "w2",
            category="other",
            is_system=False,
            is_active=False,
            expose_as_ai_tool=False,
        )
    )
    rows = adapter.list_workflows(
        database_name="default",
        category="cat",
        is_system=True,
        is_active=True,
        expose_as_ai_tool=True,
    )
    assert [r["id"] for r in rows] == ["w1"]


def test_list_workflows_by_ids(adapter: SqliteAdapter) -> None:
    adapter.create_workflow(_wf_data("w1"))
    adapter.create_workflow(_wf_data("w2"))
    adapter.create_workflow(_wf_data("w3"))
    rows = adapter.list_workflows_by_ids(["w1", "w3", "ghost"])
    assert {r["id"] for r in rows} == {"w1", "w3"}


def test_list_workflows_by_ids_empty(adapter: SqliteAdapter) -> None:
    assert adapter.list_workflows_by_ids([]) == []


# ---------------------------------------------------------------------------
# Workflow steps CRUD
# ---------------------------------------------------------------------------


def test_create_and_get_workflow_step(adapter: SqliteAdapter) -> None:
    adapter.create_workflow(_wf_data())
    created = adapter.create_workflow_step(_step_data("s1", "w1", 1))
    assert created["id"] == "s1"
    fetched = adapter.get_workflow_step("s1")
    assert fetched is not None
    assert fetched["workflow_id"] == "w1"


def test_get_workflow_step_missing(adapter: SqliteAdapter) -> None:
    assert adapter.get_workflow_step("nope") is None


def test_get_workflow_steps_ordered(adapter: SqliteAdapter) -> None:
    adapter.create_workflow(_wf_data())
    adapter.create_workflow_step(_step_data("s2", "w1", 2))
    adapter.create_workflow_step(_step_data("s1", "w1", 1))
    steps = adapter.get_workflow_steps("w1")
    assert [s["id"] for s in steps] == ["s1", "s2"]


def test_update_workflow_step(adapter: SqliteAdapter) -> None:
    adapter.create_workflow(_wf_data())
    adapter.create_workflow_step(_step_data("s1", "w1", 1))
    updated = adapter.update_workflow_step("s1", {"name": "renamed"})
    assert updated["name"] == "renamed"


def test_update_workflow_step_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.update_workflow_step("missing", {"name": "x"})


def test_delete_workflow_step(adapter: SqliteAdapter) -> None:
    adapter.create_workflow(_wf_data())
    adapter.create_workflow_step(_step_data("s1", "w1", 1))
    assert adapter.delete_workflow_step("s1") is True
    assert adapter.get_workflow_step("s1") is None


def test_delete_workflow_step_missing_returns_false(adapter: SqliteAdapter) -> None:
    assert adapter.delete_workflow_step("missing") is False


def test_delete_workflow_steps_returns_count(adapter: SqliteAdapter) -> None:
    adapter.create_workflow(_wf_data())
    adapter.create_workflow_step(_step_data("s1", "w1", 1))
    adapter.create_workflow_step(_step_data("s2", "w1", 2))
    assert adapter.delete_workflow_steps("w1") == 2
    assert adapter.get_workflow_steps("w1") == []


def test_delete_workflow_steps_empty(adapter: SqliteAdapter) -> None:
    adapter.create_workflow(_wf_data())
    assert adapter.delete_workflow_steps("w1") == 0


# ---------------------------------------------------------------------------
# Workflow statistics CRUD
# ---------------------------------------------------------------------------


def test_create_and_get_statistics(adapter: SqliteAdapter) -> None:
    adapter.create_workflow(_wf_data())
    created = adapter.create_workflow_statistics({"workflow_id": "w1", "total_executions": 3})
    assert created["total_executions"] == 3
    fetched = adapter.get_workflow_statistics("w1")
    assert fetched is not None
    assert fetched["total_executions"] == 3


def test_get_statistics_missing(adapter: SqliteAdapter) -> None:
    assert adapter.get_workflow_statistics("nope") is None


def test_update_statistics(adapter: SqliteAdapter) -> None:
    adapter.create_workflow(_wf_data())
    adapter.create_workflow_statistics({"workflow_id": "w1"})
    updated = adapter.update_workflow_statistics(
        "w1", {"total_executions": 7, "successful_executions": 5}
    )
    assert updated["total_executions"] == 7
    assert updated["successful_executions"] == 5
    assert updated["updated_at"] is not None


def test_update_statistics_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.update_workflow_statistics("missing", {"total_executions": 1})
