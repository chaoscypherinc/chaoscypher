# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for queue API handler logic.

Verifies that each handler calls the correct QueueService method with the
correct arguments and returns the service result unchanged (or transforms
it as the handler specifies). FastAPI DI is bypassed — the service mock is
passed directly as a function argument.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from chaoscypher_cortex.features.queue.api import (
    cancel_task,
    cancel_tasks,
    clear_history,
    delete_all_tasks,
    get_all_stats,
    get_health,
    get_queue_stats,
    get_task,
    get_task_result,
    list_tasks,
    queue_task,
    retry_task,
)
from chaoscypher_cortex.features.queue.models import (
    CancelAllResponse,
    CancelBatchResponse,
    CancelByMetadataResponse,
    CancelTaskResponse,
    CancelTasksRequest,
    ClearHistoryResponse,
    QueueHealthResponse,
    QueueStatsResponse,
    QueueTaskRequest,
    QueueTaskResponse,
    RetryTaskResponse,
    TaskListResponse,
    TaskResultResponse,
)
from chaoscypher_cortex.shared.api.models import PaginationMetadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_service() -> MagicMock:
    """Return a MagicMock service with async methods pre-wired."""
    service = MagicMock()
    service.enqueue_task = AsyncMock()
    service.list_tasks = AsyncMock()
    service.get_task = AsyncMock()
    service.get_task_result = AsyncMock()
    service.cancel_task = AsyncMock()
    service.retry_task = AsyncMock()
    service.cancel_batch = AsyncMock()
    service.cancel_by_metadata = AsyncMock()
    service.cancel_all_tasks = AsyncMock()
    service.clear_task_history = AsyncMock()
    service.get_all_stats = AsyncMock()
    service.get_queue_stats = AsyncMock()
    service.get_health = MagicMock()
    return service


# ---------------------------------------------------------------------------
# queue_task handler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQueueTaskHandler:
    """Tests for the queue_task (POST /tasks) handler."""

    @pytest.mark.asyncio
    async def test_forwards_request_fields(self) -> None:
        """queue_task calls service.enqueue_task with the request body fields."""
        service = _mock_service()
        service.enqueue_task.return_value = QueueTaskResponse(task_id="new-1")

        request = QueueTaskRequest(
            queue="operations",
            operation="import_ccx",
            data={"file": "x"},
            priority=20,
            metadata={"user": "a"},
        )

        result = await queue_task(
            _="test-user",
            request=request,
            queue_service=service,
        )

        assert result.task_id == "new-1"
        service.enqueue_task.assert_awaited_once_with(
            queue="operations",
            operation="import_ccx",
            data={"file": "x"},
            priority=20,
            metadata={"user": "a"},
        )


# ---------------------------------------------------------------------------
# list_tasks handler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListTasksHandler:
    """Tests for the list_tasks (GET /tasks) handler."""

    @pytest.mark.asyncio
    async def test_splits_comma_separated_queues(self) -> None:
        """list_tasks splits the 'queues' query param on commas."""
        service = _mock_service()
        service.list_tasks.return_value = TaskListResponse(
            data=[],
            pagination=PaginationMetadata(
                total=0, page=1, page_size=50, total_pages=1, has_next=False, has_prev=False
            ),
            total_in_queue=0,
            queues=["llm", "operations"],
        )

        await list_tasks(
            _="test-user",
            queue_service=service,
            pagination=(1, 50),
            queues="llm,operations",
        )

        service.list_tasks.assert_awaited_once_with(
            page=1, page_size=50, queues=["llm", "operations"]
        )

    @pytest.mark.asyncio
    async def test_none_queues_stay_none(self) -> None:
        """list_tasks passes queues=None when the query param is omitted."""
        service = _mock_service()
        service.list_tasks.return_value = TaskListResponse(
            data=[],
            pagination=PaginationMetadata(
                total=0, page=2, page_size=10, total_pages=1, has_next=False, has_prev=True
            ),
            total_in_queue=0,
            queues=None,
        )

        await list_tasks(_="test-user", queue_service=service, pagination=(2, 10), queues=None)

        service.list_tasks.assert_awaited_once_with(page=2, page_size=10, queues=None)


# ---------------------------------------------------------------------------
# get_task / get_task_result handlers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTaskHandlers:
    """Tests for the get_task and get_task_result handlers."""

    @pytest.mark.asyncio
    async def test_get_task_forwards_id(self) -> None:
        """get_task forwards the task_id to the service."""
        service = _mock_service()
        # Handler wraps the service dict into TaskDetailResponse, so the
        # mock must include every required field of that DTO.
        service.get_task.return_value = {
            "task_id": "t1",
            "queue": "operations",
            "operation": "import_ccx",
            "status": "queued",
            "priority": 50,
            "created_at": "2026-04-25T00:00:00Z",
        }

        result = await get_task(_="test-user", task_id="t1", queue_service=service)

        service.get_task.assert_awaited_once_with("t1")
        assert result.task_id == "t1"

    @pytest.mark.asyncio
    async def test_get_task_result_wraps_in_dto(self) -> None:
        """get_task_result returns the TaskResultResponse from the service."""
        service = _mock_service()
        service.get_task_result.return_value = TaskResultResponse(result={"ok": True})

        result = await get_task_result(_="test-user", task_id="t1", queue_service=service)

        service.get_task_result.assert_awaited_once_with("t1")
        assert result.result == {"ok": True}


# ---------------------------------------------------------------------------
# cancel_task / retry_task / delete_all_tasks / clear_history handlers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMutationHandlers:
    """Tests for cancel/retry/delete/clear handlers."""

    @pytest.mark.asyncio
    async def test_cancel_task_returns_dto(self) -> None:
        """cancel_task delegates to service.cancel_task and returns its DTO."""
        service = _mock_service()
        service.cancel_task.return_value = CancelTaskResponse(status="cancelled")

        result = await cancel_task(_="test-user", task_id="t1", queue_service=service)

        service.cancel_task.assert_awaited_once_with("t1")
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_retry_task_returns_dto(self) -> None:
        """retry_task delegates to service.retry_task and returns its DTO."""
        service = _mock_service()
        service.retry_task.return_value = RetryTaskResponse(
            new_task_id="new", original_task_id="old"
        )

        result = await retry_task(_="test-user", task_id="old", queue_service=service)

        service.retry_task.assert_awaited_once_with("old")
        assert result.new_task_id == "new"
        assert result.original_task_id == "old"

    @pytest.mark.asyncio
    async def test_delete_all_tasks_forwards_queue_filter(self) -> None:
        """delete_all_tasks forwards the queue query param to the service."""
        service = _mock_service()
        service.cancel_all_tasks.return_value = CancelAllResponse(cancelled=3, queue="llm")

        result = await delete_all_tasks(_="test-user", queue_service=service, queue="llm")

        service.cancel_all_tasks.assert_awaited_once_with(queue="llm")
        assert result.cancelled == 3
        assert result.queue == "llm"

    @pytest.mark.asyncio
    async def test_clear_history_forwards_args(self) -> None:
        """clear_history forwards queue and older_than_hours to the service."""
        service = _mock_service()
        service.clear_task_history.return_value = ClearHistoryResponse(
            cleared=5, queue="operations"
        )

        result = await clear_history(
            _="test-user",
            queue_service=service,
            queue="operations",
            older_than_hours=48,
        )

        service.clear_task_history.assert_awaited_once_with(queue="operations", older_than_hours=48)
        assert result.cleared == 5


# ---------------------------------------------------------------------------
# cancel_tasks (dual-mode) handler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelTasksHandler:
    """Tests for the dual-mode cancel_tasks handler (batch or metadata)."""

    @pytest.mark.asyncio
    async def test_batch_mode_uses_task_ids(self) -> None:
        """cancel_tasks uses batch mode when task_ids is supplied."""
        service = _mock_service()
        service.cancel_batch.return_value = CancelBatchResponse(
            cancelled_count=2, requested_count=2, failed=[]
        )

        result = await cancel_tasks(
            _="test-user",
            request=CancelTasksRequest(task_ids=["t1", "t2"]),
            queue_service=service,
        )

        service.cancel_batch.assert_awaited_once_with(["t1", "t2"])
        service.cancel_by_metadata.assert_not_called()
        assert isinstance(result, CancelBatchResponse)

    @pytest.mark.asyncio
    async def test_metadata_mode_when_no_task_ids(self) -> None:
        """cancel_tasks uses metadata mode when only metadata is supplied."""
        service = _mock_service()
        service.cancel_by_metadata.return_value = CancelByMetadataResponse(cancelled=4)

        result = await cancel_tasks(
            _="test-user",
            request=CancelTasksRequest(metadata={"user": "bob"}, queue="llm"),
            queue_service=service,
        )

        service.cancel_by_metadata.assert_awaited_once_with(metadata={"user": "bob"}, queue="llm")
        service.cancel_batch.assert_not_called()
        assert isinstance(result, CancelByMetadataResponse)
        assert result.cancelled == 4

    @pytest.mark.asyncio
    async def test_raises_400_when_neither_provided(self) -> None:
        """cancel_tasks raises HTTP 400 when neither task_ids nor metadata is given."""
        service = _mock_service()

        with pytest.raises(HTTPException) as exc_info:
            await cancel_tasks(
                _="test-user",
                request=CancelTasksRequest(),
                queue_service=service,
            )

        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Stats / health handlers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStatsAndHealthHandlers:
    """Tests for the stats and health handlers."""

    @pytest.mark.asyncio
    async def test_get_all_stats_returns_service_dto(self) -> None:
        """get_all_stats returns the service's QueueStatsResponse."""
        service = _mock_service()
        service.get_all_stats.return_value = QueueStatsResponse(
            queues=[{"queue": "llm", "queued": 1}],
            note="ok",
        )

        result = await get_all_stats(_="test-user", queue_service=service)

        service.get_all_stats.assert_awaited_once_with()
        assert result.queues[0]["queue"] == "llm"

    @pytest.mark.asyncio
    async def test_get_queue_stats_forwards_name(self) -> None:
        """get_queue_stats forwards the queue_name path param to the service."""
        service = _mock_service()
        # Handler wraps the service dict into QueueStatsByName, so the
        # mock must include every required field of that DTO.
        service.get_queue_stats.return_value = {
            "queue": "llm",
            "queued": 5,
            "running": 0,
        }

        result = await get_queue_stats(_="test-user", queue_name="llm", queue_service=service)

        service.get_queue_stats.assert_awaited_once_with("llm")
        assert result.queued == 5

    @pytest.mark.asyncio
    async def test_get_health_delegates(self) -> None:
        """get_health delegates to the synchronous service.get_health method."""
        service = _mock_service()
        service.get_health.return_value = QueueHealthResponse(
            status="healthy",
            enabled=True,
            connected=True,
            system="valkey",
        )

        result = await get_health(_="test-user", queue_service=service)

        service.get_health.assert_called_once_with()
        assert result.status == "healthy"
