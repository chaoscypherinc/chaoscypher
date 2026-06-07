# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR2a Task 8 — bulk workflow/step/stats methods on WorkflowStorageProtocol."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    Workflow,
    WorkflowStatistics,
    WorkflowStep,
)


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


def _seed_workflows(adapter: SqliteAdapter, entries: list[tuple[str, str]]) -> None:
    with adapter.transaction():
        for wf_id, db in entries:
            adapter.session.add(Workflow(id=wf_id, database_name=db, name=f"name-{wf_id}"))


def test_count_workflows_scoped(adapter: SqliteAdapter) -> None:
    _seed_workflows(adapter, [("w1", "a"), ("w2", "a"), ("w3", "b")])
    assert adapter.count_workflows(database_name="a") == 2
    assert adapter.count_workflows(database_name="b") == 1


def test_count_workflows_empty(adapter: SqliteAdapter) -> None:
    assert adapter.count_workflows(database_name="nonexistent") == 0


def test_delete_all_workflows_scoped(adapter: SqliteAdapter) -> None:
    _seed_workflows(adapter, [("w1", "a"), ("w2", "a"), ("w3", "b")])
    assert adapter.delete_all_workflows(database_name="a") == 2
    assert adapter.count_workflows(database_name="a") == 0
    assert adapter.count_workflows(database_name="b") == 1


def test_clear_all_workflow_steps(adapter: SqliteAdapter) -> None:
    _seed_workflows(adapter, [("w1", "a")])
    with adapter.transaction():
        adapter.session.add(
            WorkflowStep(
                id="s1",
                workflow_id="w1",
                step_number=0,
                name="step1",
                tool_type="llm",
                tool_id="llm-chat",
            )
        )
        adapter.session.add(
            WorkflowStep(
                id="s2",
                workflow_id="w1",
                step_number=1,
                name="step2",
                tool_type="llm",
                tool_id="llm-chat",
            )
        )
    assert adapter.clear_all_workflow_steps() == 2


def test_clear_all_workflow_statistics(adapter: SqliteAdapter) -> None:
    _seed_workflows(adapter, [("w1", "a")])
    with adapter.transaction():
        adapter.session.add(WorkflowStatistics(workflow_id="w1", total_executions=1))
    assert adapter.clear_all_workflow_statistics() == 1


def test_clear_all_workflow_steps_empty_noop(adapter: SqliteAdapter) -> None:
    assert adapter.clear_all_workflow_steps() == 0
