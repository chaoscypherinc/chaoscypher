# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM Queue Service - Queue Wrapper for LLMProvider.

Provides queue coordination for LLM operations in Docker app.

This service wraps LLMProvider with Valkey queue infrastructure for:
- Async background processing
- Task queuing and result waiting
- Queue statistics and monitoring
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.adapters.llm import LLMProvider, get_llm_semaphore
from chaoscypher_core.constants import QUEUE_LLM, QUEUE_OPERATIONS
from chaoscypher_core.exceptions import OperationError
from chaoscypher_core.queue import queue_client


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.ports.llm import TaskType


logger = structlog.get_logger(__name__)


class LLMQueueService:
    """Queue wrapper for LLMProvider (Docker app only).

    This service adds queue coordination to LLMProvider for use in web APIs
    and background workers. It provides:
    - Task enqueueing with priority management
    - Result waiting and polling
    - Queue statistics and monitoring
    - Handler registration for queue workers

    For direct execution without queue overhead, use LLMProvider directly.
    """

    def __init__(self, provider: LLMProvider, settings: Settings):
        """Initialize LLM queue service.

        Args:
            provider: LLMProvider instance (queue-free core logic)
            settings: Application settings

        """
        self.provider = provider
        self.settings = settings

        # Define operation handlers (delegate to provider)
        self.operation_handlers = {
            "chat_completion": self._chat_handler_wrapper,
            "tool_execution": self._tool_handler_wrapper,
        }

        logger.info("llm_queue_service_initialized")

    def register_handlers(self) -> None:
        """Register operation handlers with queue system."""
        queue_client.register_handlers(QUEUE_LLM, self.operation_handlers)  # type: ignore[arg-type]
        logger.debug(
            "llm_handlers_registered",
            handlers=list(self.operation_handlers.keys()),
        )

    # ========================================================================
    # Handler Wrappers (Delegate to Provider)
    # ========================================================================

    async def _chat_handler_wrapper(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Queue handler wrapper for chat completion.

        Delegates to provider.chat() and returns result.

        Args:
            data: Task data containing messages and parameters
            metadata: Queue metadata (unused by provider)
            task_id: Queue task ID (unused by provider)

        Returns:
            Chat completion result from provider

        """
        logger.info("chat_handler_wrapper_executing", task_id=task_id)

        # Extract parameters from data
        messages = data.get("messages", [])
        tools = data.get("tools")
        stream = data.get("stream", False)

        # Get all other kwargs (temperature, max_tokens, etc.)
        kwargs = {k: v for k, v in data.items() if k not in ["messages", "tools", "stream"]}

        # Spend-cap pre-check (2026-05-21, P0): refuse the chat call when the
        # per-day token budget is already at/over the cap. Chat is the
        # interactive (non-source-scoped) hot path so only the daily cap applies
        # — the per-source cap is skipped (source_id=None) by check_and_raise.
        # Raises permanent LLMSpendCapExceededError; classify_error routes it
        # through _execute_handler's permanent branch so the queue does NOT
        # retry.
        #
        # The daily counter is persisted per-database (llm_daily_spend) so it
        # survives worker restarts. Chat counts against the active database's
        # budget. The LLM queue worker has no request-scoped adapter (no
        # AdapterCleanupMiddleware), so open one for the current database and
        # close it in a finally — chat runs at QUEUE_LLM concurrency 1, so this
        # is one short-lived session per call (disconnect closes only the
        # session, not the shared cached engine).
        from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
        from chaoscypher_core.services.llm.spend import get_llm_spend_tracker

        db_name = self.settings.current_database
        spend_adapter = get_sqlite_adapter(db_name)
        try:
            get_llm_spend_tracker().check_and_raise(
                source_id=None,
                settings=self.settings,
                adapter=spend_adapter,
                database_name=db_name,
            )

            # Delegate to provider (queue-free core logic)
            result = await self.provider.chat(
                messages=messages, tools=tools, stream=stream, **kwargs
            )

            # Record token usage so the per-day cap observes this call.
            # Non-source-scoped (chat is interactive). Best-effort: a provider
            # response without usage data (streaming, provider quirks) still
            # completes — we just under-count by that amount until the next
            # call. ``result.usage`` is the ``TokenUsage`` Pydantic model from
            # chaoscypher_core.models.
            usage = getattr(result, "usage", None)
            if usage is not None:
                input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
                output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
                if input_tokens or output_tokens:
                    get_llm_spend_tracker().record(
                        None,
                        input_tokens + output_tokens,
                        adapter=spend_adapter,
                        database_name=db_name,
                    )

            logger.info("chat_handler_wrapper_completed", task_id=task_id)
            return result.model_dump()
        finally:
            spend_adapter.disconnect()

    async def _tool_handler_wrapper(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Queue handler wrapper for tool execution.

        Delegates to provider.execute_tool() and returns result.

        Args:
            data: Task data containing tool_name and tool_input
            metadata: Queue metadata (unused by provider)
            task_id: Queue task ID (unused by provider)

        Returns:
            Tool execution result from provider

        """
        logger.info(
            "tool_handler_wrapper_executing",
            task_id=task_id,
            tool_name=data.get("tool_name"),
        )

        # Extract parameters
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})
        kwargs = {k: v for k, v in data.items() if k not in ["tool_name", "tool_input"]}

        # Delegate to provider
        result = await self.provider.execute_tool(
            tool_name=tool_name, tool_input=tool_input, **kwargs
        )

        logger.info("tool_handler_wrapper_completed", task_id=task_id)
        return result.model_dump()

    # ========================================================================
    # Queue Operation Methods (Enqueue Tasks)
    # ========================================================================

    async def queue_operation(
        self,
        task_type: TaskType,
        operation_name: str,
        messages: list[dict[str, Any]] | None = None,
        priority: int = 50,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        """Queue an LLM operation with automatic routing based on task type.

        Args:
            task_type: Type of task (CHAT, TOOL, EMBEDDING)
            operation_name: Operation handler name (e.g., "chat_completion")
            messages: Chat messages (for chat operations)
            priority: Task priority (higher = higher priority under ZPOPMAX,
                default 50 for background; see ``PrioritySettings``)
            metadata: Optional task metadata
            **kwargs: Additional parameters (temperature, max_tokens, etc.)

        Returns:
            task_id: Unique task identifier

        """
        # All LLM operations use the single LLM queue
        queue = QUEUE_LLM

        logger.info(
            "operation_queued",
            task_type=task_type.value if hasattr(task_type, "value") else task_type,
            operation=operation_name,
            queue=queue,
            priority=priority,
        )

        return await queue_client.enqueue_task(
            queue=queue,
            operation=operation_name,
            data={
                "messages": messages,
                "task_type": (task_type.value if hasattr(task_type, "value") else str(task_type)),
                **kwargs,
            },
            priority=priority,
            metadata=metadata or {},
        )

    # ========================================================================
    # Queue Utilities (Result Waiting, Stats, etc.)
    # ========================================================================

    async def wait_for_result(self, task_id: str, timeout: float | None = None) -> Any:
        """Wait for task result with smart timeout.

        Timeout only applies to PROCESSING time (after task starts running),
        not queue wait time. This allows tasks to wait indefinitely in queue
        without timing out.

        Args:
            task_id: Task identifier
            timeout: Maximum PROCESSING time (after task starts running). If None, uses settings.timeouts.llm_operation_max

        Returns:
            Task result

        Raises:
            TimeoutError: If task processing exceeds timeout
            Exception: If task fails

        """
        # Use settings default if no timeout provided
        if timeout is None:
            timeout = self.settings.timeouts.llm_operation_max

        queue_start_time = time.monotonic()
        queue_wait_logged = False

        while True:
            task = await queue_client.get_task(task_id)
            if not task:
                msg = f"Task {task_id} not found"
                raise ValueError(msg)

            status = task["status"]

            # Task completed successfully
            if status == "completed":
                result = await queue_client.get_result(task_id)

                # Log timing information
                queue_wait_time = time.monotonic() - queue_start_time
                if task.get("started_at"):
                    # Calculate actual processing time
                    started_at = datetime.fromisoformat(task["started_at"])
                    completed_at = datetime.fromisoformat(task["completed_at"])
                    processing_time = (completed_at - started_at).total_seconds()
                    logger.debug(
                        "task_completed_timing",
                        task_id=task_id,
                        queue_wait_seconds=round(queue_wait_time, 1),
                        processing_seconds=round(processing_time, 1),
                    )

                return result

            # Task failed. ``task["error"]`` is already the public-safe,
            # client-redacted message (see queue/client.py); use it directly
            # rather than re-prefixing (which produced "Task failed: Task failed").
            if status == "failed":
                msg = task.get("error") or "Task failed"
                raise OperationError(msg)

            # Task cancelled
            if status == "cancelled":
                raise asyncio.CancelledError

            # Task is running - check PROCESSING timeout only
            if status == "running" and task.get("started_at"):
                # Calculate time since task started processing
                started_at = datetime.fromisoformat(task["started_at"])
                processing_elapsed = (datetime.now(UTC) - started_at).total_seconds()

                if processing_elapsed > timeout:
                    msg = (
                        f"Task {task_id} exceeded {timeout}s processing timeout "
                        f"(actual: {processing_elapsed:.1f}s)"
                    )
                    raise TimeoutError(msg)

                # Log queue wait time (once)
                if not queue_wait_logged:
                    queue_wait_time = time.monotonic() - queue_start_time
                    logger.info(
                        "task_started_after_queue_wait",
                        task_id=task_id,
                        queue_wait_seconds=round(queue_wait_time, 1),
                    )
                    queue_wait_logged = True

            # Task still queued - no timeout check (infinite queue wait)
            # This is the KEY change: queued tasks don't timeout

            # Poll at configurable interval (settings.timeouts.queue_poll_interval)
            await asyncio.sleep(self.settings.timeouts.queue_poll_interval)

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a task.

        Args:
            task_id: Task identifier

        Returns:
            True if cancelled, False otherwise

        """
        return await queue_client.cancel_task(task_id)

    async def get_stats(self) -> dict[str, Any]:
        """Get queue statistics with estimated completion times.

        Returns:
            Statistics for all LLM queues with estimated time to completion

        """
        stats = await queue_client.get_all_stats()

        # Filter to only LLM queue (single logical queue now)
        llm_stats = [s for s in stats if s["queue"] == QUEUE_LLM]

        # IMPORTANT: If llm queue isn't in active stats (queue is empty), create a stub entry
        # This ensures token stats are always fetched and displayed even when queue is idle
        if not llm_stats:
            llm_stats = [{"queue": QUEUE_LLM, "queued": 0, "running": 0, "workers": 1}]
            logger.debug("llm_queue_stub_entry_created")

        # Also get operations queue stats for import/workflow operations
        operations_stats = [s for s in stats if s["queue"] == QUEUE_OPERATIONS]

        # Display fields for the queue monitor UI. max_queue_depth is a soft
        # display cap — queues have no hard limit.
        max_queue_depth = self.settings.batching.queue_max_depth_display

        for stat in llm_stats:
            stat["max_depth"] = max_queue_depth
            total_items = stat.get("queued", 0) + stat.get("running", 0)
            stat["depth_percent"] = min((total_items / max_queue_depth) * 100, 100)

            # Get token stats from Valkey with custom costs if enabled
            # This always runs even when queue is empty, showing cumulative token usage
            queue_name = stat.get("queue", QUEUE_LLM)
            custom_input = (
                self.settings.llm.token_cost_input_per_million
                if self.settings.llm.enable_token_cost_tracking
                else 0.0
            )
            custom_output = (
                self.settings.llm.token_cost_output_per_million
                if self.settings.llm.enable_token_cost_tracking
                else 0.0
            )
            token_stats = await queue_client.get_token_stats(
                queue_name, custom_input, custom_output
            )
            stat.update(token_stats)

        # Format human-readable time
        def format_time(seconds: float) -> str:
            """Render seconds as a compact "Xh Ym"/"Xm Ys"/"Xs" string."""
            if seconds < 60:
                return f"{int(seconds)}s"
            if seconds < 3600:
                mins = int(seconds / 60)
                secs = int(seconds % 60)
                return f"{mins}m {secs}s" if secs > 0 else f"{mins}m"
            hours = int(seconds / 3600)
            mins = int((seconds % 3600) / 60)
            return f"{hours}h {mins}m"

        # Calculate estimated completion time based on queue depth only (no scanning!)
        # Average LLM inference time: ~15 seconds (conservative estimate)
        avg_llm_time_seconds = 15

        total_queued = sum(s["queued"] for s in llm_stats)

        # Estimate completion time based on queued tasks and worker count
        # This is an approximation since we don't scan for running tasks (too slow)
        llm_stat = next((s for s in llm_stats if s["queue"] == QUEUE_LLM), None)

        if llm_stat and llm_stat.get("queued", 0) > 0:
            workers = llm_stat.get("workers", 1)
            queued = llm_stat.get("queued", 0)

            # Estimate: Queued tasks divided by workers, times average duration
            # Assumption: workers are likely processing tasks if queue has items
            est_seconds = (queued / workers) * avg_llm_time_seconds if workers > 0 else 0
        else:
            est_seconds = 0

        # Calculate estimated completion time for operations queue (import/workflow operations)
        # Average operation execution time varies, but analysis tasks take ~30-60 seconds
        # Note: This only accounts for queued tasks, not currently running tasks
        # Frontend handles the case where estimate is 0s by not displaying it
        avg_operation_time_seconds = 45
        operations_est_seconds = 0

        if operations_stats:
            operations_stat = operations_stats[0]
            workers = operations_stat.get("workers", 1)
            queued = operations_stat.get("queued", 0)

            # Calculate based on queued tasks only (running tasks aren't counted for performance reasons)
            operations_est_seconds = (
                (queued / workers) * avg_operation_time_seconds if workers > 0 and queued > 0 else 0
            )

        # Calculate total costs and tokens across all queues
        total_cost_usd = sum(s.get("total_cost_usd", 0) for s in llm_stats)
        total_input_tokens = sum(s.get("total_input_tokens", 0) for s in llm_stats)
        total_output_tokens = sum(s.get("total_output_tokens", 0) for s in llm_stats)

        # Get semaphore statistics for real-time LLM processing visibility
        semaphore = get_llm_semaphore()
        semaphore_stats = semaphore.get_stats()

        return {
            "queues": llm_stats,
            "total_queued": total_queued,
            "total_cost_usd": total_cost_usd,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
            "estimated_completion_time_seconds": int(est_seconds),
            "estimated_completion_time_human": format_time(est_seconds),
            # Add estimated completion times per queue type
            "estimated_completion_times_human": {
                "llm": format_time(est_seconds),
                "operations": format_time(operations_est_seconds),
            },
            # Semaphore stats for real-time processing visibility
            "semaphore_stats": semaphore_stats,
        }

    async def clear_stats(self, older_than_hours: int = 24) -> None:
        """Clear all queue statistics and old completed tasks.

        Args:
            older_than_hours: Clear tasks older than this many hours (default: 24)

        """
        logger.info("clearing_statistics_started", older_than_hours=older_than_hours)

        # Clear queue statistics
        await queue_client.clear_all_stats()

        # Clear old completed tasks from queue
        cleared_count = await queue_client.clear_old_completed_tasks(
            queue=None,
            older_than_hours=older_than_hours,  # All queues
        )

        logger.info("statistics_cleared", removed_task_count=cleared_count)

    async def list_current_tasks(self, limit: int = 100) -> list:
        """List currently queued and running tasks.

        Args:
            limit: Maximum number of tasks to return

        Returns:
            List of task dictionaries

        """
        # Get recent tasks from all LLM queues
        llm_queues = [QUEUE_LLM]

        tasks = await queue_client.get_recent_tasks(limit=limit, queues=llm_queues)

        # Filter to only show queued and running tasks (not completed)
        active_tasks = [task for task in tasks if task.get("status") in ["queued", "running"]]

        logger.debug("list_current_tasks_returning", active_task_count=len(active_tasks))
        return active_tasks

    async def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        """Get status of a specific task.

        Args:
            task_id: Task identifier

        Returns:
            Task status dict or None if not found

        """
        return await queue_client.get_task(task_id)

    async def cancel_all_tasks(self) -> dict[str, Any]:
        """Cancel all queued tasks.

        Returns:
            Dictionary with cancellation results

        """
        cancelled_count = await queue_client.cancel_all_tasks(QUEUE_LLM)

        logger.info("queue_tasks_cancelled", cancelled_count=cancelled_count, queue_name=QUEUE_LLM)

        return {
            "cancelled": cancelled_count,
            "message": "Task cancellation requested for LLM queue",
        }
