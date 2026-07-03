# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for orphaned extraction task recovery."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_neuron.recovery.extraction import recover_orphaned_extraction_tasks


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
# recover_orphaned_extraction_tasks
# ============================================================================


class TestRecoverOrphanedExtractionTasks:
    """Tests for recover_orphaned_extraction_tasks."""

    @pytest.mark.asyncio
    async def test_returns_zeros_when_no_orphans(self, mock_adapter, mock_settings) -> None:
        mock_adapter.list_orphaned_chunk_tasks.return_value = []
        result = await recover_orphaned_extraction_tasks(mock_adapter, "test_db", mock_settings)
        assert result == {"recovered": 0, "skipped": 0, "failed": 0}

    @pytest.mark.asyncio
    async def test_skips_task_still_active_in_queue(self, mock_adapter, mock_settings) -> None:
        mock_adapter.list_orphaned_chunk_tasks.return_value = [
            {
                "id": "t1",
                "queue_task_id": "qt1",
                "job_id": "j1",
                "retry_count": 0,
                "max_retries": 3,
            },
        ]
        with patch("chaoscypher_neuron.recovery.extraction.queue_client") as mock_qc:
            mock_qc.client = AsyncMock()
            mock_qc.client.exists = AsyncMock(return_value=True)
            mock_qc.client.hget = AsyncMock(return_value=b"queued")
            result = await recover_orphaned_extraction_tasks(mock_adapter, "test_db", mock_settings)
        assert result["skipped"] == 1
        assert result["recovered"] == 0

    @pytest.mark.asyncio
    async def test_skips_task_with_inactive_parent_job(self, mock_adapter, mock_settings) -> None:
        mock_adapter.list_orphaned_chunk_tasks.return_value = [
            {"id": "t1", "queue_task_id": None, "job_id": "j1", "retry_count": 0, "max_retries": 3},
        ]
        mock_adapter.get_extraction_job.return_value = {"status": "completed"}
        with patch("chaoscypher_neuron.recovery.extraction.queue_client") as mock_qc:
            mock_qc.client = None
            result = await recover_orphaned_extraction_tasks(mock_adapter, "test_db", mock_settings)
        assert result["skipped"] == 1

    @pytest.mark.asyncio
    async def test_skips_task_with_no_parent_job(self, mock_adapter, mock_settings) -> None:
        mock_adapter.list_orphaned_chunk_tasks.return_value = [
            {"id": "t1", "queue_task_id": None, "job_id": "j1", "retry_count": 0, "max_retries": 3},
        ]
        mock_adapter.get_extraction_job.return_value = None
        with patch("chaoscypher_neuron.recovery.extraction.queue_client") as mock_qc:
            mock_qc.client = None
            result = await recover_orphaned_extraction_tasks(mock_adapter, "test_db", mock_settings)
        assert result["skipped"] == 1

    @pytest.mark.asyncio
    async def test_fails_task_exceeding_max_retries(self, mock_adapter, mock_settings) -> None:
        mock_adapter.list_orphaned_chunk_tasks.return_value = [
            {"id": "t1", "queue_task_id": None, "job_id": "j1", "retry_count": 3, "max_retries": 3},
        ]
        mock_adapter.get_extraction_job.return_value = {"status": "running"}
        with patch("chaoscypher_neuron.recovery.extraction.queue_client") as mock_qc:
            mock_qc.client = None
            result = await recover_orphaned_extraction_tasks(mock_adapter, "test_db", mock_settings)
        assert result["failed"] == 1
        mock_adapter.fail_chunk_task.assert_called_once_with(
            "t1", "Max retries exceeded during recovery", "max_retries"
        )
        mock_adapter.increment_job_completed_and_check.assert_called_once_with(
            job_id="j1", database_name="default", outcome="failed"
        )

    @pytest.mark.asyncio
    @patch("chaoscypher_neuron.recovery.extraction.requeue_extraction_task", new_callable=AsyncMock)
    async def test_requeues_recoverable_task(
        self, mock_requeue, mock_adapter, mock_settings
    ) -> None:
        mock_adapter.list_orphaned_chunk_tasks.return_value = [
            {
                "id": "t1",
                "queue_task_id": None,
                "job_id": "j1",
                "retry_count": 0,
                "max_retries": 3,
                "chunk_index": 5,
            },
        ]
        job = {"status": "running", "source_id": "src1"}
        mock_adapter.get_extraction_job.return_value = job
        mock_requeue.return_value = "new_qt_id"

        with patch("chaoscypher_neuron.recovery.extraction.queue_client") as mock_qc:
            mock_qc.client = None
            result = await recover_orphaned_extraction_tasks(mock_adapter, "test_db", mock_settings)

        assert result["recovered"] == 1
        mock_requeue.assert_called_once_with(
            mock_adapter,
            mock_adapter.list_orphaned_chunk_tasks.return_value[0],
            job,
            mock_settings,
        )

    @pytest.mark.asyncio
    @patch("chaoscypher_neuron.recovery.extraction.requeue_extraction_task", new_callable=AsyncMock)
    async def test_handles_requeue_failure(self, mock_requeue, mock_adapter, mock_settings) -> None:
        mock_adapter.list_orphaned_chunk_tasks.return_value = [
            {
                "id": "t1",
                "queue_task_id": None,
                "job_id": "j1",
                "retry_count": 0,
                "max_retries": 3,
                "database_name": "db1",
            },
        ]
        mock_adapter.get_extraction_job.return_value = {"status": "running"}
        mock_requeue.side_effect = Exception("queue unavailable")

        with patch("chaoscypher_neuron.recovery.extraction.queue_client") as mock_qc:
            mock_qc.client = None
            result = await recover_orphaned_extraction_tasks(mock_adapter, "test_db", mock_settings)

        assert result["failed"] == 1
        # Should attempt to mark task as failed
        mock_adapter.fail_chunk_task.assert_called_once()
        # And must advance the job-completion counter — mirroring the
        # max-retries branch — so a job whose last chunk task fails to requeue
        # still reaches its terminal check instead of hanging in "running".
        mock_adapter.increment_job_completed_and_check.assert_called_once_with(
            job_id="j1", database_name="db1", outcome="failed"
        )

    @pytest.mark.asyncio
    @patch("chaoscypher_neuron.recovery.extraction.requeue_extraction_task", new_callable=AsyncMock)
    async def test_requeue_failure_increments_even_if_fail_chunk_task_raises(
        self, mock_requeue, mock_adapter, mock_settings
    ) -> None:
        """A fail_chunk_task error must not suppress the job-counter increment.

        The increment is what unsticks the parent job, so it lives in its own
        best-effort block independent of fail_chunk_task's outcome.
        """
        mock_adapter.list_orphaned_chunk_tasks.return_value = [
            {"id": "t1", "queue_task_id": None, "job_id": "j1", "retry_count": 0, "max_retries": 3},
        ]
        mock_adapter.get_extraction_job.return_value = {"status": "running"}
        mock_requeue.side_effect = Exception("queue unavailable")
        mock_adapter.fail_chunk_task.side_effect = Exception("db write failed")

        with patch("chaoscypher_neuron.recovery.extraction.queue_client") as mock_qc:
            mock_qc.client = None
            result = await recover_orphaned_extraction_tasks(mock_adapter, "test_db", mock_settings)

        assert result["failed"] == 1
        mock_adapter.increment_job_completed_and_check.assert_called_once_with(
            job_id="j1", database_name="default", outcome="failed"
        )

    @pytest.mark.asyncio
    @patch("chaoscypher_neuron.recovery.extraction.requeue_extraction_task", new_callable=AsyncMock)
    async def test_handles_mixed_outcomes(self, mock_requeue, mock_adapter, mock_settings) -> None:
        mock_adapter.list_orphaned_chunk_tasks.return_value = [
            # Task 1: active in queue → skip
            {
                "id": "t1",
                "queue_task_id": "qt1",
                "job_id": "j1",
                "retry_count": 0,
                "max_retries": 3,
            },
            # Task 2: max retries → fail
            {"id": "t2", "queue_task_id": None, "job_id": "j2", "retry_count": 5, "max_retries": 3},
            # Task 3: recoverable → recover
            {"id": "t3", "queue_task_id": None, "job_id": "j3", "retry_count": 0, "max_retries": 3},
        ]
        mock_adapter.get_extraction_job.side_effect = [
            {"status": "running"},  # j2
            {"status": "running"},  # j3
        ]
        mock_requeue.return_value = "new_qt"

        with patch("chaoscypher_neuron.recovery.extraction.queue_client") as mock_qc:
            mock_qc.client = AsyncMock()
            mock_qc.client.exists = AsyncMock(return_value=True)
            mock_qc.client.hget = AsyncMock(return_value=b"queued")
            result = await recover_orphaned_extraction_tasks(mock_adapter, "test_db", mock_settings)

        assert result["skipped"] == 1
        assert result["failed"] == 1
        assert result["recovered"] == 1
