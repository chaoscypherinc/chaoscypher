# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for WorkflowsMixin.get_workflow_statistics_totals.

One aggregate SELECT sums execution counters across all workflows in a
database — these tests pin the summed shape, database scoping, and the
all-zero empty case (the dashboard's 2-second poll path).
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _seed_workflow_with_stats(
    adapter: SqliteAdapter,
    *,
    workflow_id: str,
    database_name: str,
    total: int,
    successful: int,
    failed: int,
    cancelled: int,
) -> None:
    """Create one workflow plus its statistics row."""
    adapter.create_workflow(
        {
            "id": workflow_id,
            "database_name": database_name,
            "name": f"wf-{workflow_id}",
            "input_schema": {},
        }
    )
    adapter.create_workflow_statistics(
        {
            "workflow_id": workflow_id,
            "total_executions": total,
            "successful_executions": successful,
            "failed_executions": failed,
            "cancelled_executions": cancelled,
        }
    )


def test_totals_sum_across_workflows(sqlite_adapter: SqliteAdapter) -> None:
    """Counters sum over every statistics row in the database."""
    _seed_workflow_with_stats(
        sqlite_adapter,
        workflow_id="w1",
        database_name="default",
        total=4,
        successful=3,
        failed=1,
        cancelled=0,
    )
    _seed_workflow_with_stats(
        sqlite_adapter,
        workflow_id="w2",
        database_name="default",
        total=6,
        successful=2,
        failed=3,
        cancelled=1,
    )
    # Workflow with no statistics row contributes nothing.
    sqlite_adapter.create_workflow(
        {
            "id": "w3",
            "database_name": "default",
            "name": "wf-w3",
            "input_schema": {},
        }
    )

    totals = sqlite_adapter.get_workflow_statistics_totals(database_name="default")

    assert totals == {
        "total_executions": 10,
        "successful_executions": 5,
        "failed_executions": 4,
        "cancelled_executions": 1,
    }


def test_totals_scoped_to_database(sqlite_adapter: SqliteAdapter) -> None:
    """Statistics of workflows in other databases are excluded."""
    _seed_workflow_with_stats(
        sqlite_adapter,
        workflow_id="w-db1",
        database_name="db1",
        total=7,
        successful=7,
        failed=0,
        cancelled=0,
    )
    _seed_workflow_with_stats(
        sqlite_adapter,
        workflow_id="w-db2",
        database_name="db2",
        total=5,
        successful=1,
        failed=4,
        cancelled=0,
    )

    totals = sqlite_adapter.get_workflow_statistics_totals(database_name="db1")

    assert totals == {
        "total_executions": 7,
        "successful_executions": 7,
        "failed_executions": 0,
        "cancelled_executions": 0,
    }


def test_totals_empty_database_all_zero(sqlite_adapter: SqliteAdapter) -> None:
    """No workflows / no statistics rows yields all-zero totals."""
    totals = sqlite_adapter.get_workflow_statistics_totals(database_name="default")

    assert totals == {
        "total_executions": 0,
        "successful_executions": 0,
        "failed_executions": 0,
        "cancelled_executions": 0,
    }
