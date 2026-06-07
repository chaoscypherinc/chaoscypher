# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for AI-tool-exposed workflow ACL (finding #9)."""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.exceptions import AuthorizationError
from chaoscypher_core.services.workflows.management.history import (
    WorkflowExecutionService,
)


class StubOps:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []

    async def enqueue_operation(
        self, operation_type: str, task_id: str, data: dict[str, Any]
    ) -> str:
        self.enqueued.append({"op": operation_type, "task_id": task_id})
        return task_id


@pytest.fixture
def adapter_with_ai_wf(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    now = datetime.now(UTC)
    a.create_workflow(
        {
            "id": "wai",
            "database_name": "default",
            "name": "ai-only",
            "is_active": True,
            "expose_as_ai_tool": True,
            "is_system": True,
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "created_at": now,
            "updated_at": now,
        }
    )
    yield a
    a.disconnect()


@pytest.mark.asyncio
async def test_ai_tool_workflow_rejects_non_admin(
    adapter_with_ai_wf: SqliteAdapter,
) -> None:
    svc = WorkflowExecutionService(
        repository=adapter_with_ai_wf,
        execution_repository=adapter_with_ai_wf,
        operations_service=StubOps(),
    )
    with pytest.raises(AuthorizationError, match="workflow:execute"):
        await svc.execute_workflow(
            "wai", {}, triggered_by="ai_tool", user={"id": 7, "is_admin": False}
        )


@pytest.mark.asyncio
async def test_ai_tool_workflow_accepts_admin(
    adapter_with_ai_wf: SqliteAdapter,
) -> None:
    ops = StubOps()
    svc = WorkflowExecutionService(
        repository=adapter_with_ai_wf,
        execution_repository=adapter_with_ai_wf,
        operations_service=ops,
    )
    eid = await svc.execute_workflow(
        "wai", {}, triggered_by="ai_tool", user={"id": 1, "is_admin": True}
    )
    assert ops.enqueued[0]["task_id"] == eid
