# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: recovery commit dispatch routes through queue_utils.queue_import_commit.

Audit fix #C4 — recovery's 'extracted' and 'committing' branches must
route through the canonical ``queue_utils.queue_import_commit`` path so
that the task metadata carries BOTH ``file_id`` AND ``source_id`` keys.
``abort_processing``'s cancel-by-metadata path (Task 5 / commit 4011c662b)
calls ``cancel_by_metadata({"file_id": source_id, "operation_type":
OP_IMPORT_COMMIT}, queue=QUEUE_OPERATIONS)`` — without ``file_id`` in
metadata the abort cancel cannot locate recovery-enqueued commit tasks.

These tests verify that _dispatch_commit delegates to
queue_utils.queue_import_commit with the expected arguments for both
the 'extracted' and 'committing' status branches.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.models import SourceStatus
from chaoscypher_core.services.sources.recovery import RecoveryStats, SourceRecovery


@pytest.mark.asyncio
async def test_extracted_branch_routes_through_queue_import_commit() -> None:
    """Recovery extracted branch delegates to queue_utils.queue_import_commit."""
    adapter = MagicMock()
    adapter.get_system_state.return_value = {"processing_paused": False}
    adapter.get_source_commit_payload.return_value = {
        "entities": [{"name": "Alice"}],
        "relationships": [],
    }

    queue_client = AsyncMock()
    queue_client.task_exists_for_source = AsyncMock(return_value=False)

    service = SourceRecovery(adapter=adapter, queue_client=queue_client)
    service._is_recently_active = MagicMock(return_value=False)

    source = {
        "id": "src_extracted",
        "status": SourceStatus.EXTRACTED,
        "filepath": "/data/test.txt",
        "filename": "test.txt",
        "file_type": "text",
        "is_paused": False,
        "recovery_attempts": 0,
        "commit_complete": False,
    }
    stats = RecoveryStats()

    with patch(
        "chaoscypher_core.services.sources.recovery.queue_utils.queue_import_commit",
        new_callable=AsyncMock,
    ) as mock_qic:
        mock_qic.return_value = "task_123"
        await service._recover_one(
            source=source,
            database_name="default",
            stats=stats,
            respect_stall_threshold=True,
        )

    mock_qic.assert_awaited_once()
    _, kwargs = mock_qic.await_args
    assert kwargs["file_id"] == "src_extracted"
    assert kwargs["commit_data"]["entities"] == [{"name": "Alice"}]
    assert kwargs["database_name"] == "default"
    assert kwargs["extra_metadata"] == {"triggered_by": "recovery"}


@pytest.mark.asyncio
async def test_committing_branch_routes_through_queue_import_commit() -> None:
    """Recovery committing branch delegates to queue_utils.queue_import_commit."""
    adapter = MagicMock()
    adapter.get_system_state.return_value = {"processing_paused": False}
    adapter.get_source_commit_payload.return_value = {
        "entities": [{"name": "Bob"}],
        "relationships": [{"source": "Bob", "target": "Alice", "type": "knows"}],
    }

    queue_client = AsyncMock()
    queue_client.task_exists_for_source = AsyncMock(return_value=False)

    service = SourceRecovery(adapter=adapter, queue_client=queue_client)
    service._is_recently_active = MagicMock(return_value=False)

    source = {
        "id": "src_committing",
        "status": SourceStatus.COMMITTING,
        "filepath": "/data/other.txt",
        "filename": "other.txt",
        "file_type": "text",
        "is_paused": False,
        "recovery_attempts": 0,
        "commit_complete": False,
    }
    stats = RecoveryStats()

    with patch(
        "chaoscypher_core.services.sources.recovery.queue_utils.queue_import_commit",
        new_callable=AsyncMock,
    ) as mock_qic:
        mock_qic.return_value = "task_456"
        await service._recover_one(
            source=source,
            database_name="default",
            stats=stats,
            respect_stall_threshold=True,
        )

    mock_qic.assert_awaited_once()
    _, kwargs = mock_qic.await_args
    assert kwargs["file_id"] == "src_committing"
    assert kwargs["commit_data"]["entities"] == [{"name": "Bob"}]
    assert kwargs["database_name"] == "default"
    assert kwargs["extra_metadata"] == {"triggered_by": "recovery"}
