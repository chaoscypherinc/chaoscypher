# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for allow_parallel_execution enforcement (finding #5)."""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.exceptions import WorkflowBusyError
from chaoscypher_core.services.workflows.management.history import (
    WorkflowExecutionService,
)


class StubOps:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_operation(
        self, operation_type: str, task_id: str, data: dict[str, Any]
    ) -> str:
        self.calls.append({"op": operation_type, "task_id": task_id})
        return task_id


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    now = datetime.now(UTC)
    a.create_workflow(
        {
            "id": "w1",
            "database_name": "default",
            "name": "nopar",
            "is_active": True,
            "allow_parallel_execution": False,
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "created_at": now,
            "updated_at": now,
        }
    )
    a.create_execution(
        {
            "id": "e_running",
            "workflow_id": "w1",
            "triggered_by": "test",
            "inputs": {},
            "status": "running",
            "created_at": now,
        }
    )
    yield a
    a.disconnect()


@pytest.mark.asyncio
async def test_rejects_concurrent_run_when_parallel_disabled(
    adapter: SqliteAdapter,
) -> None:
    ops = StubOps()
    svc = WorkflowExecutionService(
        repository=adapter, execution_repository=adapter, operations_service=ops
    )
    with pytest.raises(WorkflowBusyError):
        await svc.execute_workflow("w1", {}, triggered_by="manual")
    assert ops.calls == []
