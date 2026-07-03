# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``OperationsRepository.enqueue_operation`` metadata composition.

Canonical fields (``task_id``, ``operation_type``, ``database_name``) are
stamped last so a caller can never override them via ``extra_metadata``,
while every other extra key flows through to the queue task untouched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.operations.repository import OperationsRepository


async def _capture_metadata(*, extra_metadata, database_name=None) -> dict:
    repo = OperationsRepository()
    with patch(
        "chaoscypher_core.operations.repository.queue_client.enqueue_task",
        new=AsyncMock(return_value="qt1"),
    ) as mock_enqueue:
        await repo.enqueue_operation(
            operation_type="execute_workflow",
            task_id="exec_123",
            data={"k": "v"},
            database_name=database_name,
            extra_metadata=extra_metadata,
        )
    mock_enqueue.assert_awaited_once()
    kwargs = mock_enqueue.await_args.kwargs
    assert kwargs["queue"] == QUEUE_OPERATIONS
    return kwargs["metadata"]


@pytest.mark.asyncio
async def test_extra_metadata_cannot_override_canonical_fields() -> None:
    metadata = await _capture_metadata(
        database_name="real_db",
        extra_metadata={
            "task_id": "spoofed",
            "operation_type": "spoofed",
            "database_name": "spoofed",
            "user_id": "u1",
        },
    )
    # Canonical fields win over the caller-supplied spoofs.
    assert metadata["task_id"] == "exec_123"
    assert metadata["operation_type"] == "execute_workflow"
    assert metadata["database_name"] == "real_db"
    # Non-canonical extras flow through.
    assert metadata["user_id"] == "u1"


@pytest.mark.asyncio
async def test_extra_metadata_database_name_used_when_none_resolved() -> None:
    """With no resolved database, a caller-supplied database_name survives."""
    metadata = await _capture_metadata(
        database_name=None,
        extra_metadata={"database_name": "from_extra"},
    )
    assert metadata["database_name"] == "from_extra"


@pytest.mark.asyncio
async def test_no_extra_metadata_yields_only_canonical_fields() -> None:
    metadata = await _capture_metadata(database_name="real_db", extra_metadata=None)
    assert metadata == {
        "task_id": "exec_123",
        "operation_type": "execute_workflow",
        "database_name": "real_db",
    }
