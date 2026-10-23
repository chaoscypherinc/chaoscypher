# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A user cancel must stop a running workflow and stay ``cancelled``.

Cortex's cancel stamps the ``workflow_executions`` row ``cancelled`` and sets
the queue's cooperative cancel flag, which no workflow code reads. Before the
fix the run carried on through every remaining step, then the worker's
unconditional ``finalize_execution(COMPLETED)`` overwrote ``cancelled``; a
cancel landing between pickup and the RUNNING write was resurrected too.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import Workflow
from chaoscypher_core.operations.workflows import orchestrator
from chaoscypher_core.operations.workflows.orchestrator import execute_workflow_task
from chaoscypher_core.operations.workflows.repository import WorkflowExecutionRepository
from chaoscypher_core.operations.workflows.status import WorkflowExecutionStatus
from chaoscypher_core.services.workflows.engine.validator import WorkflowValidator


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "cc-test" / "app.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(get_engine(str(path)), checkfirst=True)
    return path


@pytest.fixture
def adapters(db_path: Path) -> Generator[tuple[SqliteAdapter, SqliteAdapter]]:
    """Two independent sessions: the worker's and Cortex's."""
    worker = SqliteAdapter(str(db_path), database_name="test")
    cortex = SqliteAdapter(str(db_path), database_name="test")
    worker.connect()
    cortex.connect()
    with worker.transaction():
        worker.session.add(Workflow(id="wf1", database_name="test", name="wf", input_schema={}))
    worker.create_execution(
        {
            "id": "exec-1",
            "workflow_id": "wf1",
            "triggered_by": "manual",
            "inputs": {},
            "status": "pending",
        }
    )
    yield worker, cortex
    worker.disconnect()
    cortex.disconnect()


class TestRepositoryKeepsCancel:
    def test_finalize_does_not_overwrite_cancelled(
        self, adapters: tuple[SqliteAdapter, SqliteAdapter]
    ) -> None:
        worker, cortex = adapters
        repo = WorkflowExecutionRepository(worker)
        assert repo.update_status("exec-1", WorkflowExecutionStatus.RUNNING) is True

        cortex.update_status("exec-1", "cancelled")  # the Cancel button

        # The worker sees the cancel on its own (identity-mapped) session...
        assert repo.get_status("exec-1") == WorkflowExecutionStatus.CANCELLED
        # ...and its late finalize must not flip the row back to completed.
        assert (
            repo.finalize_execution(
                "exec-1",
                status=WorkflowExecutionStatus.COMPLETED,
                outputs={"x": 1},
                error_message=None,
                failed_step_id=None,
                duration_ms=5,
            )
            is False
        )
        assert cortex.get_execution("exec-1")["status"] == "cancelled"

    def test_running_write_does_not_resurrect_cancelled(
        self, adapters: tuple[SqliteAdapter, SqliteAdapter]
    ) -> None:
        worker, cortex = adapters
        cortex.update_status("exec-1", "cancelled")

        repo = WorkflowExecutionRepository(worker)
        assert repo.update_status("exec-1", WorkflowExecutionStatus.RUNNING) is False
        assert repo.get_status("exec-1") == WorkflowExecutionStatus.CANCELLED

    def test_finalize_still_writes_a_running_row(
        self, adapters: tuple[SqliteAdapter, SqliteAdapter]
    ) -> None:
        worker, cortex = adapters
        repo = WorkflowExecutionRepository(worker)
        repo.update_status("exec-1", WorkflowExecutionStatus.RUNNING)
        assert repo.finalize_execution(
            "exec-1",
            status=WorkflowExecutionStatus.COMPLETED,
            outputs={"x": 1},
            error_message=None,
            failed_step_id=None,
            duration_ms=5,
        )
        row = cortex.get_execution("exec-1")
        assert row["status"] == "completed"
        assert row["outputs"] == {"x": 1}
        assert row["started_at"] is not None


def _workflow_service() -> MagicMock:
    svc = MagicMock()
    svc.get_workflow.return_value = {"id": "wf-1", "name": "T", "is_active": True}
    svc.list_workflow_steps.return_value = [
        {"step_number": 1, "tool_type": "system_tool", "tool_id": "t1"}
    ]
    return svc


async def _run(compiled: MagicMock, *, claimed: bool, status: str) -> tuple[dict, MagicMock]:
    graph = MagicMock()
    graph.compile.return_value = compiled
    with (
        patch(
            "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
            return_value=MagicMock(),
        ),
        patch.object(WorkflowValidator, "validate_workflow", staticmethod(lambda w: [])),
        patch.object(WorkflowValidator, "validate_inputs", staticmethod(lambda w, i: [])),
        patch.object(WorkflowExecutionRepository, "get_execution", return_value={"id": "exec-1"}),
        patch.object(WorkflowExecutionRepository, "update_status", return_value=claimed),
        patch.object(WorkflowExecutionRepository, "get_status", return_value=status),
        patch.object(WorkflowExecutionRepository, "finalize_execution") as finalize,
        patch.object(orchestrator, "build_workflow_graph", return_value=graph),
        patch.object(orchestrator, "CANCEL_POLL_INTERVAL_SECS", 0.01),
    ):
        result = await execute_workflow_task(
            workflow_id="wf-1",
            inputs={},
            workflow_service=_workflow_service(),
            tool_service=None,
            llm_service=AsyncMock(),
            graph_repository=MagicMock(),
            search_repository=MagicMock(),
            database_name="test_db",
            execution_id="exec-1",
        )
    return result, finalize


class TestOrchestratorHonoursCancel:
    @pytest.mark.asyncio
    async def test_running_graph_is_stopped_when_row_turns_cancelled(self) -> None:
        stopped = asyncio.Event()

        async def _long_run(_state: object) -> dict:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                stopped.set()
                raise
            return {"step_results": {}}

        compiled = MagicMock()
        compiled.ainvoke = _long_run

        res, finalize = await asyncio.wait_for(
            _run(compiled, claimed=True, status=WorkflowExecutionStatus.CANCELLED),
            timeout=5,
        )

        assert stopped.is_set()
        assert res["success"] is False
        finalize.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_before_pickup_runs_no_step(self) -> None:
        compiled = MagicMock()
        compiled.ainvoke = AsyncMock(return_value={"step_results": {}})

        res, finalize = await _run(
            compiled, claimed=False, status=WorkflowExecutionStatus.CANCELLED
        )

        compiled.ainvoke.assert_not_called()
        assert res["success"] is False
        finalize.assert_not_called()

    @pytest.mark.asyncio
    async def test_uncancelled_run_completes(self) -> None:
        compiled = MagicMock()
        compiled.ainvoke = AsyncMock(return_value={"step_results": {}})

        res, finalize = await _run(compiled, claimed=True, status=WorkflowExecutionStatus.RUNNING)

        assert res["success"] is True
        assert finalize.call_args.kwargs["status"] == WorkflowExecutionStatus.COMPLETED
