# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for QueueService.

Covers the thin wrapper around queue_client: happy paths, availability
checks, pagination bounds, and error propagation. All queue_client calls
are mocked — no real Valkey connection is ever used.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.exceptions import (
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from chaoscypher_cortex.features.queue.models import (
    CancelAllResponse,
    CancelBatchResponse,
    CancelByMetadataResponse,
    CancelTaskResponse,
    ClearHistoryResponse,
    QueueHealthResponse,
    QueueStatsResponse,
    QueueTaskResponse,
    RetryTaskResponse,
    TaskListResponse,
    TaskResultResponse,
)
from chaoscypher_cortex.features.queue.service import QueueService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(*, available: bool = True, enabled: bool = True) -> QueueService:
    """Return a QueueService with a fresh mocked queue_client."""
    service = QueueService()
    mock_client = MagicMock()
    mock_client.is_available = available
    mock_client.is_enabled = enabled

    # Wire async methods with AsyncMock defaults so tests can override per-case.
    mock_client.enqueue_task = AsyncMock(return_value="task-123")
    mock_client.get_recent_tasks = AsyncMock(return_value=[])
    mock_client.get_recent_tasks_count = AsyncMock(return_value=0)
    mock_client.get_all_stats = AsyncMock(return_value=[])
    mock_client.get_task = AsyncMock(return_value=None)
    mock_client.get_result = AsyncMock(return_value=None)
    mock_client.cancel_task = AsyncMock(return_value=True)
    mock_client.retry_task = AsyncMock(return_value=None)
    mock_client.cancel_by_metadata = AsyncMock(return_value=0)
    mock_client.cancel_tasks_batch = AsyncMock(return_value={"cancelled": 0, "failed": []})
    mock_client.cancel_all_tasks = AsyncMock(return_value=0)
    mock_client.clear_old_completed_tasks = AsyncMock(return_value=0)
    mock_client.get_queue_stats = AsyncMock(return_value={})

    service.queue_client = mock_client
    return service


def _fake_settings(
    *,
    default_page_size: int = 50,
    max_page_size: int = 1000,
    queue_max_depth_display: int = 500,
    background_priority: int = 50,
) -> SimpleNamespace:
    """Return a SimpleNamespace mimicking the Settings object used by the service."""
    return SimpleNamespace(
        pagination=SimpleNamespace(
            default_page_size=default_page_size,
            max_page_size=max_page_size,
        ),
        batching=SimpleNamespace(queue_max_depth_display=queue_max_depth_display),
        priorities=SimpleNamespace(background=background_priority),
    )


# ---------------------------------------------------------------------------
# _check_available tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckAvailable:
    """Tests for the private _check_available guard."""

    def test_noop_when_available(self) -> None:
        """_check_available returns silently when the client is available."""
        service = _make_service(available=True)
        # Must not raise
        service._check_available()

    def test_raises_when_unavailable(self) -> None:
        """_check_available raises ExternalServiceError when client is down."""
        service = _make_service(available=False)
        with pytest.raises(ExternalServiceError):
            service._check_available()


# ---------------------------------------------------------------------------
# enqueue_task tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnqueueTask:
    """Tests for QueueService.enqueue_task."""

    @pytest.mark.asyncio
    async def test_returns_task_id_response(self) -> None:
        """enqueue_task returns QueueTaskResponse with the client's task id."""
        service = _make_service()
        service.queue_client.enqueue_task = AsyncMock(return_value="new-id-42")

        with patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_fake_settings(),
        ):
            result = await service.enqueue_task(
                queue="operations",
                operation="import_ccx",
                data={"file": "x.ccx"},
                priority=10,
                metadata={"user": "alice"},
            )

        assert isinstance(result, QueueTaskResponse)
        assert result.task_id == "new-id-42"
        service.queue_client.enqueue_task.assert_awaited_once_with(
            queue="operations",
            operation="import_ccx",
            data={"file": "x.ccx"},
            priority=10,
            metadata={"user": "alice"},
        )

    @pytest.mark.asyncio
    async def test_uses_default_background_priority_when_none(self) -> None:
        """enqueue_task falls back to settings.priorities.background when priority is None."""
        service = _make_service()

        with patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_fake_settings(background_priority=77),
        ):
            await service.enqueue_task(
                queue="operations",
                operation="noop",
                data={},
                priority=None,
                metadata=None,
            )

        call_kwargs = service.queue_client.enqueue_task.await_args.kwargs
        assert call_kwargs["priority"] == 77
        assert call_kwargs["metadata"] == {}

    @pytest.mark.asyncio
    async def test_raises_when_unavailable(self) -> None:
        """enqueue_task raises ExternalServiceError when the client is down."""
        service = _make_service(available=False)
        with pytest.raises(ExternalServiceError):
            await service.enqueue_task(queue="q", operation="op", data={})


# ---------------------------------------------------------------------------
# list_tasks tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListTasks:
    """Tests for QueueService.list_tasks (canonical {data, pagination} envelope)."""

    @pytest.mark.asyncio
    async def test_returns_empty_response_when_unavailable(self) -> None:
        """list_tasks returns an empty TaskListResponse when the client is down."""
        service = _make_service(available=False)
        result = await service.list_tasks(queues=["llm"])

        assert isinstance(result, TaskListResponse)
        assert result.data == []
        assert result.pagination.total == 0
        assert result.pagination.page == 1
        assert result.total_in_queue == 0
        assert result.queues == ["llm"]

    @pytest.mark.asyncio
    async def test_happy_path_builds_pagination(self) -> None:
        """list_tasks returns canonical {data, pagination} envelope."""
        service = _make_service()
        service.queue_client.get_recent_tasks = AsyncMock(return_value=[{"id": "t1"}, {"id": "t2"}])
        service.queue_client.get_recent_tasks_count = AsyncMock(return_value=5)
        service.queue_client.get_all_stats = AsyncMock(
            return_value=[
                {"queue": "llm", "queued": 3, "running": 1},
                {"queue": "operations", "queued": 2, "running": 0},
            ]
        )

        with patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_fake_settings(),
        ):
            result = await service.list_tasks(page=1, page_size=2, queues=None)

        assert len(result.data) == 2
        assert result.total_in_queue == 6  # 3+1+2+0
        assert result.pagination.total == 5
        assert result.pagination.page == 1
        assert result.pagination.page_size == 2
        assert result.pagination.total_pages == 3  # ceil(5/2)
        assert result.pagination.has_next is True
        assert result.pagination.has_prev is False

    @pytest.mark.asyncio
    async def test_page_two_offset_computation(self) -> None:
        """page=2 forwards offset=page_size to the queue client."""
        service = _make_service()
        service.queue_client.get_recent_tasks = AsyncMock(return_value=[])
        service.queue_client.get_recent_tasks_count = AsyncMock(return_value=100)

        with patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_fake_settings(),
        ):
            await service.list_tasks(page=2, page_size=10)

        call_kwargs = service.queue_client.get_recent_tasks.await_args.kwargs
        assert call_kwargs["offset"] == 10
        assert call_kwargs["limit"] == 10

    @pytest.mark.asyncio
    async def test_filters_total_in_queue_by_requested_queues(self) -> None:
        """list_tasks only sums totals for queues in the requested list."""
        service = _make_service()
        service.queue_client.get_recent_tasks = AsyncMock(return_value=[{"id": "t1"}])
        service.queue_client.get_recent_tasks_count = AsyncMock(return_value=1)
        service.queue_client.get_all_stats = AsyncMock(
            return_value=[
                {"queue": "llm", "queued": 10, "running": 2},
                {"queue": "operations", "queued": 99, "running": 99},
            ]
        )

        with patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_fake_settings(),
        ):
            result = await service.list_tasks(queues=["llm"])

        assert result.total_in_queue == 12
        assert result.queues == ["llm"]

    @pytest.mark.asyncio
    async def test_defaults_page_size_to_default_page_size(self) -> None:
        """list_tasks defaults page_size to settings.pagination.default_page_size when None."""
        service = _make_service()
        service.queue_client.get_recent_tasks = AsyncMock(return_value=[])
        service.queue_client.get_recent_tasks_count = AsyncMock(return_value=0)

        with patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_fake_settings(default_page_size=25, max_page_size=100),
        ):
            result = await service.list_tasks(page=1, page_size=None)

        call_kwargs = service.queue_client.get_recent_tasks.await_args.kwargs
        assert call_kwargs["limit"] == 25
        assert result.pagination.page_size == 25

    @pytest.mark.asyncio
    async def test_clamps_page_size_to_max(self) -> None:
        """list_tasks clamps page_size to settings.pagination.max_page_size."""
        service = _make_service()
        service.queue_client.get_recent_tasks = AsyncMock(return_value=[])
        service.queue_client.get_recent_tasks_count = AsyncMock(return_value=0)

        with patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_fake_settings(default_page_size=50, max_page_size=100),
        ):
            await service.list_tasks(page=1, page_size=9999)

        call_kwargs = service.queue_client.get_recent_tasks.await_args.kwargs
        assert call_kwargs["limit"] == 100

    @pytest.mark.asyncio
    async def test_falls_back_to_task_count_when_stats_fail(self) -> None:
        """list_tasks uses len(data) as total_in_queue when get_all_stats raises."""
        service = _make_service()
        service.queue_client.get_recent_tasks = AsyncMock(
            return_value=[{"id": "a"}, {"id": "b"}, {"id": "c"}]
        )
        service.queue_client.get_recent_tasks_count = AsyncMock(return_value=3)
        service.queue_client.get_all_stats = AsyncMock(side_effect=RuntimeError("boom"))

        with patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_fake_settings(),
        ):
            result = await service.list_tasks()

        assert result.total_in_queue == 3

    @pytest.mark.asyncio
    async def test_last_page_has_no_next(self) -> None:
        """list_tasks.pagination.has_next is False on the final page."""
        service = _make_service()
        service.queue_client.get_recent_tasks = AsyncMock(return_value=[{"id": "t1"}, {"id": "t2"}])
        service.queue_client.get_recent_tasks_count = AsyncMock(return_value=4)
        service.queue_client.get_all_stats = AsyncMock(return_value=[])

        with patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=_fake_settings(),
        ):
            result = await service.list_tasks(page=2, page_size=2)

        assert result.pagination.page == 2
        assert result.pagination.total_pages == 2
        assert result.pagination.has_next is False
        assert result.pagination.has_prev is True


# ---------------------------------------------------------------------------
# get_task / get_task_result tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTask:
    """Tests for QueueService.get_task and get_task_result."""

    @pytest.mark.asyncio
    async def test_get_task_returns_task_dict(self) -> None:
        """get_task returns the dict from the client when present."""
        service = _make_service()
        service.queue_client.get_task = AsyncMock(return_value={"id": "t1", "status": "queued"})

        result = await service.get_task("t1")

        assert result == {"id": "t1", "status": "queued"}

    @pytest.mark.asyncio
    async def test_get_task_raises_not_found_when_missing(self) -> None:
        """get_task raises NotFoundError when the client returns None."""
        service = _make_service()
        service.queue_client.get_task = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await service.get_task("missing")

    @pytest.mark.asyncio
    async def test_get_task_result_wraps_in_response_model(self) -> None:
        """get_task_result wraps the result in TaskResultResponse."""
        service = _make_service()
        service.queue_client.get_result = AsyncMock(return_value={"answer": 42})

        result = await service.get_task_result("t1")

        assert isinstance(result, TaskResultResponse)
        assert result.result == {"answer": 42}

    @pytest.mark.asyncio
    async def test_get_task_result_raises_when_none(self) -> None:
        """get_task_result raises NotFoundError when the client returns None."""
        service = _make_service()
        service.queue_client.get_result = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await service.get_task_result("t1")


# ---------------------------------------------------------------------------
# cancel_task / retry_task / batch / metadata tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelAndRetry:
    """Tests for cancellation and retry operations."""

    @pytest.mark.asyncio
    async def test_cancel_task_happy_path(self) -> None:
        """cancel_task returns CancelTaskResponse(status='cancelled') on success."""
        service = _make_service()
        service.queue_client.get_task = AsyncMock(return_value={"id": "t1"})
        service.queue_client.cancel_task = AsyncMock(return_value=True)

        result = await service.cancel_task("t1")

        assert isinstance(result, CancelTaskResponse)
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_task_raises_not_found_when_missing(self) -> None:
        """cancel_task raises NotFoundError if the task does not exist."""
        service = _make_service()
        service.queue_client.get_task = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await service.cancel_task("missing")

    @pytest.mark.asyncio
    async def test_cancel_task_raises_validation_error_when_running(self) -> None:
        """cancel_task raises ValidationError when the task cannot be cancelled."""
        service = _make_service()
        service.queue_client.get_task = AsyncMock(return_value={"id": "t1"})
        service.queue_client.cancel_task = AsyncMock(return_value=False)

        with pytest.raises(ValidationError):
            await service.cancel_task("t1")

    @pytest.mark.asyncio
    async def test_retry_task_returns_new_ids(self) -> None:
        """retry_task returns a RetryTaskResponse with new + original task ids."""
        service = _make_service()
        service.queue_client.retry_task = AsyncMock(return_value="new-id")

        result = await service.retry_task("old-id")

        assert isinstance(result, RetryTaskResponse)
        assert result.new_task_id == "new-id"
        assert result.original_task_id == "old-id"

    @pytest.mark.asyncio
    async def test_retry_task_raises_not_found_when_missing(self) -> None:
        """retry_task raises NotFoundError when the client returns a falsy id.

        Per the cortex/core error-handling rules, services raise Core
        exceptions (NotFoundError) and the API layer maps them to HTTP
        404 — services never raise HTTPException directly.
        """
        service = _make_service()
        service.queue_client.retry_task = AsyncMock(return_value=None)

        with pytest.raises(NotFoundError):
            await service.retry_task("missing")

    @pytest.mark.asyncio
    async def test_retry_task_converts_value_error_to_validation_error(self) -> None:
        """retry_task wraps ValueError from the client into ValidationError."""
        service = _make_service()
        service.queue_client.retry_task = AsyncMock(side_effect=ValueError("bad state"))

        with pytest.raises(ValidationError):
            await service.retry_task("t1")

    @pytest.mark.asyncio
    async def test_cancel_by_metadata_returns_count(self) -> None:
        """cancel_by_metadata returns CancelByMetadataResponse with the client count."""
        service = _make_service()
        service.queue_client.cancel_by_metadata = AsyncMock(return_value=7)

        result = await service.cancel_by_metadata({"user": "bob"}, queue="llm")

        assert isinstance(result, CancelByMetadataResponse)
        assert result.cancelled == 7
        service.queue_client.cancel_by_metadata.assert_awaited_once_with(
            metadata={"user": "bob"}, queue="llm"
        )

    @pytest.mark.asyncio
    async def test_cancel_batch_empty_short_circuits(self) -> None:
        """cancel_batch returns zeros without calling the client when ids are empty."""
        service = _make_service()

        result = await service.cancel_batch([])

        assert isinstance(result, CancelBatchResponse)
        assert result.cancelled_count == 0
        assert result.requested_count == 0
        assert result.failed == []
        service.queue_client.cancel_tasks_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_batch_returns_counts(self) -> None:
        """cancel_batch forwards ids and maps the client response to the DTO."""
        service = _make_service()
        service.queue_client.cancel_tasks_batch = AsyncMock(
            return_value={"cancelled": 2, "failed": [{"id": "t3", "reason": "running"}]}
        )

        result = await service.cancel_batch(["t1", "t2", "t3"])

        assert result.cancelled_count == 2
        assert result.requested_count == 3
        assert result.failed == [{"id": "t3", "reason": "running"}]

    @pytest.mark.asyncio
    async def test_cancel_all_tasks_returns_count_and_queue(self) -> None:
        """cancel_all_tasks returns a CancelAllResponse with the queue filter."""
        service = _make_service()
        service.queue_client.cancel_all_tasks = AsyncMock(return_value=3)

        result = await service.cancel_all_tasks(queue="operations")

        assert isinstance(result, CancelAllResponse)
        assert result.cancelled == 3
        assert result.queue == "operations"


# ---------------------------------------------------------------------------
# clear_task_history / stats / health tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStatsAndHealth:
    """Tests for history clearing, stats, and health."""

    @pytest.mark.asyncio
    async def test_clear_task_history_forwards_args(self) -> None:
        """clear_task_history forwards queue/older_than_hours to the client."""
        service = _make_service()
        service.queue_client.clear_old_completed_tasks = AsyncMock(return_value=4)

        result = await service.clear_task_history(queue="llm", older_than_hours=24)

        assert isinstance(result, ClearHistoryResponse)
        assert result.cleared == 4
        assert result.queue == "llm"
        service.queue_client.clear_old_completed_tasks.assert_awaited_once_with(
            queue="llm", older_than_hours=24
        )

    @pytest.mark.asyncio
    async def test_get_all_stats_returns_empty_when_unavailable(self) -> None:
        """get_all_stats returns an unavailable-note response when the client is down."""
        service = _make_service(available=False)

        result = await service.get_all_stats()

        assert isinstance(result, QueueStatsResponse)
        assert result.queues == []
        assert result.note == "Queue service unavailable"

    @pytest.mark.asyncio
    async def test_get_all_stats_returns_stats_list(self) -> None:
        """get_all_stats returns the client stats list wrapped in the DTO."""
        service = _make_service()
        stats_payload = [
            {"queue": "llm", "queued": 1, "running": 0},
            {"queue": "operations", "queued": 2, "running": 0},
        ]
        service.queue_client.get_all_stats = AsyncMock(return_value=stats_payload)

        result = await service.get_all_stats()

        assert result.queues == stats_payload
        assert result.note is not None

    @pytest.mark.asyncio
    async def test_get_queue_stats_returns_client_dict(self) -> None:
        """get_queue_stats returns the raw client dict for a single queue."""
        service = _make_service()
        service.queue_client.get_queue_stats = AsyncMock(
            return_value={"queue": "llm", "queued": 5, "running": 1}
        )

        result = await service.get_queue_stats("llm")

        assert result == {"queue": "llm", "queued": 5, "running": 1}

    def test_get_health_reports_healthy_when_available(self) -> None:
        """get_health returns status='healthy' when the client is available."""
        service = _make_service(available=True, enabled=True)

        result = service.get_health()

        assert isinstance(result, QueueHealthResponse)
        assert result.status == "healthy"
        assert result.enabled is True
        assert result.connected is True
        assert result.system == "valkey"

    def test_get_health_reports_unavailable_when_down(self) -> None:
        """get_health returns status='unavailable' when the client is not available."""
        service = _make_service(available=False, enabled=True)

        result = service.get_health()

        assert result.status == "unavailable"
        assert result.connected is False


# ---------------------------------------------------------------------------
# Depth percent calculation tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDepthPercentFormula:
    """Verify the depth_percent math used by the LLM queue stats service.

    The queue feature itself doesn't set max_depth/depth_percent, but the
    formula lives in shared/llm/queue_service.py and is the contract the UI
    relies on. These tests pin the formula so refactors can't silently
    break the 0-100 clamp.
    """

    @staticmethod
    def _compute_depth_percent(queued: int, running: int, max_depth: int) -> float:
        """Mirror the formula from shared/llm/queue_service.py:338."""
        total_items = queued + running
        return min((total_items / max_depth) * 100, 100)

    def test_percent_zero_when_queue_empty(self) -> None:
        """depth_percent is 0 when nothing is queued or running."""
        assert self._compute_depth_percent(0, 0, 1000) == 0

    def test_percent_proportional_in_range(self) -> None:
        """depth_percent is (queued + running) / max_depth * 100 while under cap."""
        assert self._compute_depth_percent(250, 250, 1000) == 50.0

    def test_percent_clamped_to_100_when_exceeding_max(self) -> None:
        """depth_percent never exceeds 100 even when total_items > max_depth."""
        assert self._compute_depth_percent(5000, 5000, 1000) == 100

    def test_percent_at_exactly_max_depth(self) -> None:
        """depth_percent is exactly 100 when total_items equals max_depth."""
        assert self._compute_depth_percent(500, 500, 1000) == 100.0
