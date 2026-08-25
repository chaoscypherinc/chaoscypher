# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for orphaned extraction task recovery."""

from unittest.mock import ANY, AsyncMock, MagicMock, patch

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
            groups_by_source=ANY,
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


class TestRecoveryDerivesGroupsAndJobOnce:
    """Startup recovery must not re-derive per-source work once per task.

    ``get_hierarchical_groups`` reads every DocumentChunk row of the source,
    JSON-parses each row's metadata and materialises each group's whole
    ``combined_content`` — then the caller keeps ONE group and discards the
    rest. Recovering K orphaned tasks of one source used to pay that K times
    (plus one identical ``get_extraction_job`` PK lookup per task), inside the
    blocking worker-startup hook.
    """

    @staticmethod
    def _tasks(count: int, *, job_id: str = "j1", db: str = "db1") -> list[dict]:
        return [
            {
                "id": f"t{i}",
                "queue_task_id": None,
                "job_id": job_id,
                "database_name": db,
                "retry_count": 0,
                "max_retries": 3,
                "chunk_index": i,
            }
            for i in range(count)
        ]

    @staticmethod
    def _groups(count: int) -> list[dict]:
        return [
            {"id": f"grp-{i}", "group_index": i, "small_chunk_ids": [f"c{i}"]} for i in range(count)
        ]

    @pytest.mark.asyncio
    async def test_groups_and_job_derived_once_per_source(
        self, mock_adapter, mock_settings
    ) -> None:
        """Five orphaned tasks of one source → ONE groups call, ONE job lookup."""
        mock_adapter.list_orphaned_chunk_tasks.return_value = self._tasks(5)
        mock_adapter.get_extraction_job.return_value = {"status": "running", "source_id": "src1"}
        mock_adapter.get_hierarchical_groups.return_value = self._groups(5)

        mock_service = MagicMock()
        mock_service.queue_extract_chunk = AsyncMock(return_value="new-qt")

        with (
            patch("chaoscypher_neuron.recovery.extraction.queue_client") as mock_qc,
            patch(
                "chaoscypher_core.operations.extraction.ChunkExtractionOperationsService",
                return_value=mock_service,
            ),
        ):
            mock_qc.client = None
            result = await recover_orphaned_extraction_tasks(mock_adapter, "db1", mock_settings)

        assert result["recovered"] == 5
        assert mock_adapter.get_hierarchical_groups.call_count == 1
        assert mock_adapter.get_extraction_job.call_count == 1
        # Every task still resolved its OWN group (the cache is a lookup, not a
        # shortcut): each requeue carries the group whose index matches.
        queued = [
            c.kwargs["hierarchical_group_id"]
            for c in mock_service.queue_extract_chunk.call_args_list
        ]
        assert queued == [f"grp-{i}" for i in range(5)]

    @pytest.mark.asyncio
    async def test_cache_is_keyed_per_source_and_job(self, mock_adapter, mock_settings) -> None:
        """Two sources/jobs each derive their own groups exactly once."""
        mock_adapter.list_orphaned_chunk_tasks.return_value = [
            *self._tasks(2, job_id="j1", db="db1"),
            *self._tasks(2, job_id="j2", db="db1"),
        ]
        mock_adapter.get_extraction_job.side_effect = lambda job_id: {
            "j1": {"status": "running", "source_id": "src1"},
            "j2": {"status": "running", "source_id": "src2"},
        }[job_id]
        mock_adapter.get_hierarchical_groups.return_value = self._groups(2)

        mock_service = MagicMock()
        mock_service.queue_extract_chunk = AsyncMock(return_value="new-qt")

        with (
            patch("chaoscypher_neuron.recovery.extraction.queue_client") as mock_qc,
            patch(
                "chaoscypher_core.operations.extraction.ChunkExtractionOperationsService",
                return_value=mock_service,
            ),
        ):
            mock_qc.client = None
            result = await recover_orphaned_extraction_tasks(mock_adapter, "db1", mock_settings)

        assert result["recovered"] == 4
        assert mock_adapter.get_hierarchical_groups.call_count == 2
        assert mock_adapter.get_extraction_job.call_count == 2
        sources_derived = [
            c.kwargs["source_id"] for c in mock_adapter.get_hierarchical_groups.call_args_list
        ]
        assert sources_derived == ["src1", "src2"]

    @pytest.mark.asyncio
    async def test_mark_queued_failure_cancels_the_enqueued_task(
        self, mock_adapter, mock_settings
    ) -> None:
        """A persist failure after enqueue must cancel the live queue task.

        Regression: ``queue_extract_chunk`` succeeded, then
        ``mark_chunk_task_queued`` raised — the caller marked the chunk task
        terminally failed in SQLite while the queue still held (and would
        execute) the new task. The compensating ``cancel_task`` keeps the
        two stores in agreement.
        """
        from chaoscypher_neuron.recovery.extraction import requeue_extraction_task

        task = self._tasks(1)[0]
        job = {"status": "running", "source_id": "src1"}
        mock_adapter.get_hierarchical_groups.return_value = self._groups(1)
        mock_adapter.mark_chunk_task_queued.side_effect = RuntimeError("db locked")

        mock_service = MagicMock()
        mock_service.queue_extract_chunk = AsyncMock(return_value="new-qt")

        with (
            patch("chaoscypher_neuron.recovery.extraction.queue_client") as mock_qc,
            patch(
                "chaoscypher_core.operations.extraction.ChunkExtractionOperationsService",
                return_value=mock_service,
            ),
        ):
            mock_qc.cancel_task = AsyncMock(return_value=True)
            with pytest.raises(RuntimeError, match="db locked"):
                await requeue_extraction_task(mock_adapter, task, job, mock_settings)

        mock_qc.cancel_task.assert_awaited_once_with("new-qt")
