# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``OperationsRepository.abort_operation`` actually cancels queue tasks.

Pre-fix: ``abort_operation`` raised ``NotImplementedError`` and the
``cancel_execution`` caller swallowed the exception and flipped the row
to ``cancelled``. The queue task kept running and burned LLM tokens.

Post-fix: ``abort_operation`` delegates to
``queue_client.cancel_by_metadata({"task_id": execution_id},
queue=QUEUE_OPERATIONS)`` — queued tasks get removed; running tasks
get a cancel flag the worker honours between batches.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.operations.repository import OperationsRepository


@pytest.mark.asyncio
async def test_abort_operation_calls_cancel_by_metadata_with_execution_id() -> None:
    repo = OperationsRepository()

    with patch(
        "chaoscypher_core.operations.repository.queue_client.cancel_by_metadata",
        new=AsyncMock(return_value=1),
    ) as mock_cancel:
        result = await repo.abort_operation("exec_abc123")

    mock_cancel.assert_awaited_once_with(
        metadata={"task_id": "exec_abc123"},
        queue=QUEUE_OPERATIONS,
    )
    assert result is True


@pytest.mark.asyncio
async def test_abort_operation_returns_false_when_no_task_found() -> None:
    """If the operation already completed (or never enqueued), return False."""
    repo = OperationsRepository()

    with patch(
        "chaoscypher_core.operations.repository.queue_client.cancel_by_metadata",
        new=AsyncMock(return_value=0),
    ):
        result = await repo.abort_operation("exec_already_done")

    assert result is False
