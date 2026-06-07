# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR2a Task 10 — bulk trigger methods on TriggerStorageProtocol."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    Trigger,
    TriggerExecutionRow,
    Workflow,
    WorkflowExecution,
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


def _seed_trigger(adapter: SqliteAdapter, tid: str, db: str) -> None:
    wf_id = f"wf-{tid}"
    with adapter.transaction():
        adapter.session.add(Workflow(id=wf_id, database_name=db, name=f"wf-{tid}-name"))
    with adapter.transaction():
        adapter.session.add(
            Trigger(
                id=tid,
                database_name=db,
                name=f"t-{tid}",
                event_source="node.created",
                filters={},
                workflow_id=wf_id,
            )
        )


def test_count_triggers_scoped(adapter: SqliteAdapter) -> None:
    _seed_trigger(adapter, "t1", "a")
    _seed_trigger(adapter, "t2", "b")
    assert adapter.count_triggers(database_name="a") == 1
    assert adapter.count_triggers(database_name="b") == 1


def test_count_triggers_empty(adapter: SqliteAdapter) -> None:
    assert adapter.count_triggers(database_name="nonexistent") == 0


def test_delete_all_triggers_scoped(adapter: SqliteAdapter) -> None:
    _seed_trigger(adapter, "t1", "a")
    _seed_trigger(adapter, "t2", "a")
    _seed_trigger(adapter, "t3", "b")
    assert adapter.delete_all_triggers(database_name="a") == 2
    assert adapter.count_triggers(database_name="b") == 1


def test_clear_all_trigger_executions(adapter: SqliteAdapter) -> None:
    _seed_trigger(adapter, "t1", "a")
    with adapter.transaction():
        for i in range(3):
            adapter.session.add(
                WorkflowExecution(
                    id=f"we-{i}", workflow_id="wf-t1", triggered_by="test", status="completed"
                )
            )
    with adapter.transaction():
        for i in range(3):
            adapter.session.add(
                TriggerExecutionRow(
                    id=f"te-{i}",
                    trigger_id="t1",
                    workflow_execution_id=f"we-{i}",
                    event_data={},
                    status="success",
                )
            )
    assert adapter.clear_all_trigger_executions() == 3


def test_clear_all_trigger_executions_empty_noop(adapter: SqliteAdapter) -> None:
    assert adapter.clear_all_trigger_executions() == 0
