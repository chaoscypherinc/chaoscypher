# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for atomic finalize_execution (finding #4).

The repository now takes a connected ``SqliteAdapter`` directly and
relies on ``adapter.transaction()`` for commit/rollback (CC011), so
the older ``get_db_session`` indirection no longer applies.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.operations.workflows.repository import (
    WorkflowExecutionRepository,
)
from chaoscypher_core.operations.workflows.status import WorkflowExecutionStatus


@pytest.fixture
def seeded_repo(tmp_path: Path) -> Generator[WorkflowExecutionRepository]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    now = datetime.now(UTC)
    adapter.create_workflow(
        {
            "id": "w1",
            "database_name": "default",
            "name": "wf",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "created_at": now,
            "updated_at": now,
        }
    )
    adapter.create_execution(
        {
            "id": "e1",
            "workflow_id": "w1",
            "triggered_by": "test",
            "inputs": {},
            "status": "pending",
            "created_at": now,
        }
    )

    try:
        yield WorkflowExecutionRepository(adapter=adapter)
    finally:
        adapter.disconnect()


def test_finalize_execution_sets_all_completion_fields(
    seeded_repo: WorkflowExecutionRepository,
) -> None:
    seeded_repo.finalize_execution(
        execution_id="e1",
        status=WorkflowExecutionStatus.COMPLETED,
        outputs={"result": "ok"},
        error_message=None,
        failed_step_id=None,
        duration_ms=42,
    )
    got = seeded_repo.get_execution("e1")
    assert got is not None
    assert got["status"] == WorkflowExecutionStatus.COMPLETED
    assert got["outputs"] == {"result": "ok"}
    assert got["duration_ms"] == 42
    assert got["completed_at"] is not None


def test_finalize_execution_sets_all_failure_fields(
    seeded_repo: WorkflowExecutionRepository,
) -> None:
    seeded_repo.finalize_execution(
        execution_id="e1",
        status=WorkflowExecutionStatus.FAILED,
        outputs=None,
        error_message="boom",
        failed_step_id="s2",
        duration_ms=99,
    )
    got = seeded_repo.get_execution("e1")
    assert got is not None
    assert got["status"] == WorkflowExecutionStatus.FAILED
    assert got["error_message"] == "boom"
    assert got["failed_step_id"] == "s2"
    assert got["duration_ms"] == 99
