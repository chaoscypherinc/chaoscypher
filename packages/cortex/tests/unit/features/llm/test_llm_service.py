# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for LLMService."""

from unittest.mock import AsyncMock, patch

import pytest

from chaoscypher_core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from chaoscypher_cortex.features.llm.models import (
    CancelAllTasksResponse,
    ClearSemaphoreResponse,
    LLMStatsResponse,
    LLMTasksResponse,
    LLMTaskStatusResponse,
)
from chaoscypher_cortex.features.llm.service import LLMService


pytestmark = pytest.mark.asyncio


@pytest.fixture
def llm_manager():
    """Create a mock LLM queue manager with async methods."""
    manager = AsyncMock()
    manager.is_available = True
    return manager


@pytest.fixture
def llm_service(llm_manager):
    """Create an LLMService with a mocked manager."""
    return LLMService(llm_manager)


@pytest.mark.unit
class TestLLMService:
    """Tests for LLMService queue monitoring and management operations."""

    # ------------------------------------------------------------------ #
    # get_stats
    # ------------------------------------------------------------------ #

    async def test_get_stats_success(self, llm_service, llm_manager):
        stats_data = {
            "total_tasks": 42,
            "completed": 40,
            "failed": 2,
            "avg_latency_ms": 150.5,
        }
        llm_manager.get_stats.return_value = stats_data

        result = await llm_service.get_stats()

        assert isinstance(result, LLMStatsResponse)
        assert result.data == stats_data
        llm_manager.get_stats.assert_awaited_once()

    async def test_get_stats_unavailable(self):
        service = LLMService(llm_manager=None)

        with pytest.raises(ExternalServiceError):
            await service.get_stats()

    # ------------------------------------------------------------------ #
    # clear_stats
    # ------------------------------------------------------------------ #

    async def test_clear_stats_success(self, llm_service, llm_manager):
        llm_manager.clear_stats.return_value = None

        await llm_service.clear_stats(older_than_hours=24)

        llm_manager.clear_stats.assert_awaited_once_with(older_than_hours=24)

    async def test_clear_stats_unavailable(self):
        service = LLMService(llm_manager=None)

        with pytest.raises(ExternalServiceError):
            await service.clear_stats(older_than_hours=12)

    # ------------------------------------------------------------------ #
    # list_current_tasks
    # ------------------------------------------------------------------ #

    async def test_list_current_tasks(self, llm_service, llm_manager):
        tasks_data = [
            {"id": "task-1", "status": "running", "type": "chat"},
            {"id": "task-2", "status": "queued", "type": "embedding"},
        ]
        llm_manager.list_current_tasks.return_value = tasks_data

        result = await llm_service.list_current_tasks()

        assert isinstance(result, LLMTasksResponse)
        assert result.data == tasks_data
        assert len(result.data) == 2
        llm_manager.list_current_tasks.assert_awaited_once()

    async def test_list_current_tasks_unavailable(self):
        service = LLMService(llm_manager=None)

        with pytest.raises(ExternalServiceError):
            await service.list_current_tasks()

    # ------------------------------------------------------------------ #
    # get_task_status
    # ------------------------------------------------------------------ #

    async def test_get_task_status_found(self, llm_service, llm_manager):
        task_status = {
            "id": "task-abc",
            "status": "running",
            "progress": 0.75,
            "type": "chat",
        }
        llm_manager.get_task_status.return_value = task_status

        result = await llm_service.get_task_status("task-abc")

        assert isinstance(result, LLMTaskStatusResponse)
        assert result.data == task_status
        llm_manager.get_task_status.assert_awaited_once_with("task-abc")

    async def test_get_task_status_not_found(self, llm_service, llm_manager):
        llm_manager.get_task_status.return_value = None

        with pytest.raises(NotFoundError):
            await llm_service.get_task_status("nonexistent-task")

    # ------------------------------------------------------------------ #
    # cancel_task
    # ------------------------------------------------------------------ #

    async def test_cancel_task_success(self, llm_service, llm_manager):
        llm_manager.cancel_task.return_value = True

        await llm_service.cancel_task("task-xyz")

        llm_manager.cancel_task.assert_awaited_once_with("task-xyz")

    async def test_cancel_task_not_found(self, llm_service, llm_manager):
        llm_manager.cancel_task.return_value = False

        with pytest.raises(ValidationError):
            await llm_service.cancel_task("already-done-task")

    # ------------------------------------------------------------------ #
    # cancel_all_tasks
    # ------------------------------------------------------------------ #

    async def test_cancel_all_tasks(self, llm_service, llm_manager):
        cancel_result = {"cancelled": 5, "failed": 0}
        llm_manager.cancel_all_tasks.return_value = cancel_result

        result = await llm_service.cancel_all_tasks()

        assert isinstance(result, CancelAllTasksResponse)
        assert result.data == cancel_result
        llm_manager.cancel_all_tasks.assert_awaited_once()

    # ------------------------------------------------------------------ #
    # clear_semaphore
    # ------------------------------------------------------------------ #

    async def test_clear_semaphore(self, llm_service):
        semaphore_data = {"cleared": 2, "queues_reset": ["llm_chat", "llm_embed"]}

        with patch(
            "chaoscypher_cortex.features.llm.service.clear_llm_semaphore_waiting_queues",
            new_callable=AsyncMock,
            return_value=semaphore_data,
        ):
            result = await llm_service.clear_semaphore()

        assert isinstance(result, ClearSemaphoreResponse)
        assert result.data == semaphore_data

    # ------------------------------------------------------------------ #
    # _check_available
    # ------------------------------------------------------------------ #

    def test_check_available_returns_manager(self, llm_service, llm_manager):
        result = llm_service._check_available()

        assert result is llm_manager

    def test_check_available_raises_when_none(self):
        service = LLMService(llm_manager=None)

        with pytest.raises(ExternalServiceError):
            service._check_available()
