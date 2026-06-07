# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Additional coverage for extraction task recovery.

Complements ``test_extraction_recovery.py`` by exercising
``requeue_extraction_task`` (happy path + missing-group ValueError) and the
``recover_orphaned_extraction_tasks`` missing-id skip branch.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_neuron.recovery.extraction import (
    recover_orphaned_extraction_tasks,
    requeue_extraction_task,
)


@pytest.fixture
def mock_adapter():
    """Create a mock SQLite adapter."""
    return MagicMock()


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = MagicMock()
    settings.priorities.background = 50
    return settings


# ============================================================================
# requeue_extraction_task
# ============================================================================


class TestRequeueExtractionTask:
    """Tests for requeue_extraction_task."""

    @pytest.mark.asyncio
    async def test_requeue_happy_path(self, mock_adapter, mock_settings) -> None:
        """A matching hierarchical group drives a clean re-queue."""
        task = {
            "id": "t1",
            "chunk_index": 5,
            "job_id": "j1",
            "database_name": "mydb",
            "retry_count": 2,
        }
        job = {"source_id": "src1"}

        # get_hierarchical_groups returns a group whose group_index matches
        # the task's chunk_index.
        group = {
            "id": "grp-5",
            "group_index": 5,
            "small_chunk_ids": ["c1", "c2"],
        }
        mock_adapter.get_hierarchical_groups.return_value = [
            {"id": "grp-0", "group_index": 0},
            group,
        ]

        mock_service = MagicMock()
        mock_service.queue_extract_chunk = AsyncMock(return_value="new-qt-id")

        with patch(
            "chaoscypher_core.operations.extraction.ChunkExtractionOperationsService",
            return_value=mock_service,
        ) as mock_cls:
            result = await requeue_extraction_task(mock_adapter, task, job, mock_settings)

        assert result == "new-qt-id"

        # The chunk task was updated with the incremented retry count and the
        # matched hierarchical group id.
        mock_adapter.update_chunk_task.assert_called_once()
        upd_args = mock_adapter.update_chunk_task.call_args
        assert upd_args.args[0] == "t1"
        updates = upd_args.args[1]
        assert updates["retry_count"] == 3  # 2 + 1
        assert updates["hierarchical_group_id"] == "grp-5"
        assert updates["status"] == "pending"
        assert updates["queue_task_id"] is None

        # The service was constructed with the adapter as source_repository.
        mock_cls.assert_called_once_with(source_repository=mock_adapter)

        # queue_extract_chunk got the matched group id + small chunk ids.
        qec_kwargs = mock_service.queue_extract_chunk.call_args.kwargs
        assert qec_kwargs["chunk_task_id"] == "t1"
        assert qec_kwargs["job_id"] == "j1"
        assert qec_kwargs["chunk_index"] == 5
        assert qec_kwargs["hierarchical_group_id"] == "grp-5"
        assert qec_kwargs["small_chunk_ids"] == ["c1", "c2"]
        assert qec_kwargs["priority"] == 50

        # Finally the task is marked queued with the new id.
        mock_adapter.mark_chunk_task_queued.assert_called_once_with("t1", "new-qt-id")

    @pytest.mark.asyncio
    async def test_requeue_raises_when_no_matching_group(self, mock_adapter, mock_settings) -> None:
        """No hierarchical group with the task's chunk_index raises ValueError."""
        task = {
            "id": "t1",
            "chunk_index": 99,  # no group matches this index
            "job_id": "j1",
            "database_name": "mydb",
            "retry_count": 0,
        }
        job = {"source_id": "src1"}

        mock_adapter.get_hierarchical_groups.return_value = [
            {"id": "grp-0", "group_index": 0},
            {"id": "grp-1", "group_index": 1},
        ]

        with pytest.raises(ValueError, match="Could not find hierarchical group"):
            await requeue_extraction_task(mock_adapter, task, job, mock_settings)

        # We never got as far as updating or re-queuing the task.
        mock_adapter.update_chunk_task.assert_not_called()
        mock_adapter.mark_chunk_task_queued.assert_not_called()


# ============================================================================
# recover_orphaned_extraction_tasks — missing id skip
# ============================================================================


class TestRecoverOrphanedMissingId:
    """Tests for the missing job_id / task id skip branch."""

    @pytest.mark.asyncio
    async def test_skips_task_with_missing_job_id(self, mock_adapter, mock_settings) -> None:
        """An orphaned task whose job_id is None is skipped before job lookup."""
        mock_adapter.list_orphaned_chunk_tasks.return_value = [
            {
                "id": "t1",
                "queue_task_id": None,
                "job_id": None,  # missing parent job id
                "retry_count": 0,
                "max_retries": 3,
            },
        ]

        with patch("chaoscypher_neuron.recovery.extraction.queue_client") as mock_qc:
            mock_qc.client = None
            result = await recover_orphaned_extraction_tasks(mock_adapter, "test_db", mock_settings)

        assert result["skipped"] == 1
        assert result["recovered"] == 0
        assert result["failed"] == 0
        # The missing-id branch short-circuits before the job lookup.
        mock_adapter.get_extraction_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_task_with_missing_task_id(self, mock_adapter, mock_settings) -> None:
        """An orphaned task with no ``id`` is skipped before job lookup."""
        mock_adapter.list_orphaned_chunk_tasks.return_value = [
            {
                # no "id" key — task_id resolves to None
                "queue_task_id": None,
                "job_id": "j1",
                "retry_count": 0,
                "max_retries": 3,
            },
        ]

        with patch("chaoscypher_neuron.recovery.extraction.queue_client") as mock_qc:
            mock_qc.client = None
            result = await recover_orphaned_extraction_tasks(mock_adapter, "test_db", mock_settings)

        assert result["skipped"] == 1
        mock_adapter.get_extraction_job.assert_not_called()
