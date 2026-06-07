# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR2a Task 11 — clear_all_workflow_executions."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import Workflow, WorkflowExecution


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


def test_clear_all_workflow_executions(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        adapter.session.add(Workflow(id="wf1", database_name="test", name="n"))
    with adapter.transaction():
        for i in range(3):
            adapter.session.add(
                WorkflowExecution(
                    id=f"e-{i}",
                    workflow_id="wf1",
                    triggered_by="manual",
                    status="completed",
                )
            )
    assert adapter.clear_all_workflow_executions() == 3


def test_clear_all_workflow_executions_empty_noop(adapter: SqliteAdapter) -> None:
    assert adapter.clear_all_workflow_executions() == 0
