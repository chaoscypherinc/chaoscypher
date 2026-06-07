# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM Priority Semaphore - Funnel for all LLM API calls.

This semaphore sits in front of ALL LLM calls (streaming and queued) and ensures:
1. Only N concurrent LLM requests (configurable, default=1 for local Ollama)
2. Interactive chat gets priority over background tools
3. Reserved slots for high-priority requests

Architecture:
    Streaming Chat → acquire(high_priority=True)  → LLM API → release()
    Queue Workers  → acquire(high_priority=False) → LLM API → release()
"""

import asyncio
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import structlog


logger = structlog.get_logger(__name__)


class PrioritySemaphore:
    """Semaphore that grants slots to high-priority requests first.

    Controls concurrent access to LLM APIs with priority-based queuing.
    Ensures interactive chat requests get priority over background
    tasks, while respecting the max concurrent limit (typically 1 for
    local Ollama to prevent slowdown).

    High-priority requests (interactive chat) jump ahead of low-priority
    requests (background tools/workflows) in the queue. Reserved slots
    ensure high-priority requests always have access even when low-priority
    requests are queued.

    Attributes:
        max_concurrent: Maximum concurrent LLM requests allowed
        reserved_high_priority: Number of slots reserved for high-priority requests
        high_priority_waiters: Queue of waiting high-priority requests
        low_priority_waiters: Queue of waiting low-priority requests
        active_count: Total number of active requests
        active_high_priority: Number of active high-priority requests
        active_low_priority: Number of active low-priority requests
        lock: Asyncio lock for thread-safe operations
        total_high_priority: Total high-priority requests processed
        total_low_priority: Total low-priority requests processed
        total_wait_time_high: Cumulative wait time for high-priority requests (seconds)
        total_wait_time_low: Cumulative wait time for low-priority requests (seconds)

    Example:
        >>> from chaoscypher_core.adapters.llm.limit import get_llm_semaphore
        >>>
        >>> # Initialize semaphore (usually done once at startup)
        >>> semaphore = get_llm_semaphore(
        ...     max_concurrent=1,
        ...     reserved_high_priority=1
        ... )
        >>>
        >>> # Interactive chat (high priority)
        >>> async def handle_chat():
        ...     async with semaphore.acquire(high_priority=True):
        ...         response = await llm_provider.chat(messages)
        ...         return response
        >>>
        >>> # Background tool (low priority)
        >>> async def extract_entities():
        ...     async with semaphore.acquire(high_priority=False):
        ...         result = await llm_provider.chat(extraction_prompt)
        ...         return result
        >>>
        >>> # Get statistics
        >>> stats = semaphore.get_stats()
        >>> print(f"Active: {stats['active_count']}/{stats['max_concurrent']}")
        >>> print(f"Waiting: {stats['waiting_high_priority']} high, {stats['waiting_low_priority']} low")

    Note:
        For local Ollama, max_concurrent=1 is recommended to avoid
        performance degradation. Cloud providers (OpenAI, Anthropic)
        can handle higher concurrency (e.g., 10-50).

    """

    def __init__(self, max_concurrent: int = 1, reserved_high_priority: int = 1):
        """Initialize priority semaphore.

        Args:
            max_concurrent: Maximum concurrent LLM requests (default 1 for Ollama)
            reserved_high_priority: Slots reserved for high-priority requests

        """
        self.max_concurrent = max_concurrent
        self.reserved_high_priority = min(reserved_high_priority, max_concurrent)

        # Waiting queues (each entry is an Event to signal when slot is granted)
        self.high_priority_waiters: asyncio.Queue[asyncio.Event] = asyncio.Queue()
        self.low_priority_waiters: asyncio.Queue[asyncio.Event] = asyncio.Queue()

        # Events of waiters cancelled before their slot was granted. asyncio.Queue
        # has no random removal, so we tombstone the event here and skip it when
        # _try_grant_slots pops it — otherwise the grant would inflate
        # active_count for a waiter that no longer exists (a leaked slot).
        self._abandoned: set[asyncio.Event] = set()

        # Active slot tracking
        self.active_count = 0
        self.active_high_priority = 0
        self.active_low_priority = 0

        # Lock for thread-safe operations
        self.lock = asyncio.Lock()

        # Metrics
        self.total_high_priority = 0
        self.total_low_priority = 0
        self.total_wait_time_high = 0.0
        self.total_wait_time_low = 0.0

        logger.info(
            "llm_semaphore_initialized",
            max_concurrent=max_concurrent,
            reserved_high_priority=reserved_high_priority,
            low_priority_limit=max_concurrent - reserved_high_priority,
        )

    @asynccontextmanager
    async def acquire(self, high_priority: bool = False) -> AsyncGenerator[None]:
        """Acquire an LLM slot with priority-based queuing.

        Args:
            high_priority: If True, jumps ahead of low-priority requests

        Usage:
            async with semaphore.acquire(high_priority=True):
                response = await llm_api_call()

        """
        start_time = datetime.now(UTC)

        # Create an event that will be signaled when we get a slot
        my_event = asyncio.Event()

        # Add ourselves to the appropriate queue
        if high_priority:
            await self.high_priority_waiters.put(my_event)
        else:
            await self.low_priority_waiters.put(my_event)

        # Try to grant slots (might grant to us immediately)
        await self._try_grant_slots()

        # Wait until we're granted a slot. If we're cancelled here (request
        # timeout / client disconnect) the `try/finally` below never runs, so
        # we must clean up our slot accounting before the CancelledError
        # propagates — otherwise the slot leaks.
        try:
            await my_event.wait()
        except asyncio.CancelledError:
            await self._handle_cancelled_wait(my_event, high_priority)
            raise

        # Track wait time
        wait_time = (datetime.now(UTC) - start_time).total_seconds()
        priority_label = "HIGH" if high_priority else "LOW"

        if high_priority:
            self.total_wait_time_high += wait_time
            if wait_time > 0.1:
                logger.info(
                    "llm_slot_acquired_after_wait",
                    priority=priority_label,
                    wait_seconds=round(wait_time, 2),
                )
            else:
                logger.info("llm_slot_acquired_immediately", priority=priority_label)
        else:
            self.total_wait_time_low += wait_time
            if wait_time > 0.5:
                logger.info(
                    "llm_slot_acquired_after_wait",
                    priority=priority_label,
                    wait_seconds=round(wait_time, 2),
                )
            else:
                logger.info("llm_slot_acquired_immediately", priority=priority_label)

        try:
            yield
        finally:
            # Release the slot when done
            await self._release_slot(high_priority)

    async def _try_grant_slots(self) -> None:
        """Grant slots to waiting requests (high priority first).

        This is called:
        1. When a new request arrives
        2. When a slot is released
        """
        async with self.lock:
            while self.active_count < self.max_concurrent:
                granted = False

                # Try to grant to high-priority first
                if not self.high_priority_waiters.empty():
                    event = await self.high_priority_waiters.get()
                    if event in self._abandoned:
                        # Waiter was cancelled before grant — skip without
                        # consuming a slot, then look at the next waiter.
                        self._abandoned.discard(event)
                        continue
                    event.set()
                    self.active_count += 1
                    self.active_high_priority += 1
                    self.total_high_priority += 1
                    granted = True
                    logger.info(
                        "llm_slot_granted_high_priority",
                        active_count=self.active_count,
                        max_concurrent=self.max_concurrent,
                        waiting_high=self.high_priority_waiters.qsize(),
                        waiting_low=self.low_priority_waiters.qsize(),
                    )

                # Try to grant to low-priority (but respect reserved slots)
                elif not self.low_priority_waiters.empty():
                    # Calculate available slots for low-priority
                    # (total - reserved for high priority)
                    available_for_low = self.max_concurrent - self.reserved_high_priority

                    if self.active_count < available_for_low:
                        event = await self.low_priority_waiters.get()
                        if event in self._abandoned:
                            # Cancelled waiter — skip without consuming a slot.
                            self._abandoned.discard(event)
                            continue
                        event.set()
                        self.active_count += 1
                        self.active_low_priority += 1
                        self.total_low_priority += 1
                        granted = True
                        logger.info(
                            "llm_slot_granted_low_priority",
                            active_count=self.active_count,
                            available_for_low=available_for_low,
                            waiting_high=self.high_priority_waiters.qsize(),
                            waiting_low=self.low_priority_waiters.qsize(),
                        )
                    else:
                        # Low-priority can't use reserved slots
                        break

                if not granted:
                    break

    def _release_slot_locked(self, was_high_priority: bool) -> None:
        """Decrement active-slot counters. Caller must hold ``self.lock``."""
        self.active_count -= 1

        if was_high_priority:
            self.active_high_priority -= 1
        else:
            self.active_low_priority -= 1

        priority_label = "HIGH" if was_high_priority else "LOW"
        logger.info(
            "llm_slot_released",
            priority=priority_label,
            active_count=self.active_count,
            max_concurrent=self.max_concurrent,
            waiting_high=self.high_priority_waiters.qsize(),
            waiting_low=self.low_priority_waiters.qsize(),
        )

    async def _release_slot(self, was_high_priority: bool) -> None:
        """Release a slot and try to grant to next waiter.

        Args:
            was_high_priority: Whether the released slot was high-priority

        """
        async with self.lock:
            self._release_slot_locked(was_high_priority)

        # Try to grant the freed slot to next waiter
        await self._try_grant_slots()

    async def _handle_cancelled_wait(self, my_event: asyncio.Event, high_priority: bool) -> None:
        """Clean up slot accounting when a waiter is cancelled mid-``acquire``.

        Two cases, distinguished under the lock so they stay consistent with
        ``_try_grant_slots`` (which sets the event + increments under the same
        lock):

        * Slot already granted to us (``my_event`` is set): we will never run
          the ``finally`` that releases it, so release it here and hand the
          freed slot to the next waiter.
        * Still queued (``my_event`` not set): tombstone it so the eventual
          grant skips it instead of inflating ``active_count``.
        """
        regrant = False
        async with self.lock:
            if my_event.is_set():
                self._release_slot_locked(high_priority)
                regrant = True
            else:
                self._abandoned.add(my_event)

        if regrant:
            await self._try_grant_slots()

    def get_stats(self) -> dict[str, int | float]:
        """Get semaphore statistics."""
        return {
            "max_concurrent": self.max_concurrent,
            "reserved_high_priority": self.reserved_high_priority,
            "active_count": self.active_count,
            "active_high_priority": self.active_high_priority,
            "active_low_priority": self.active_low_priority,
            "waiting_high_priority": self.high_priority_waiters.qsize(),
            "waiting_low_priority": self.low_priority_waiters.qsize(),
            "total_high_priority": self.total_high_priority,
            "total_low_priority": self.total_low_priority,
            "avg_wait_time_high": (
                self.total_wait_time_high / self.total_high_priority
                if self.total_high_priority > 0
                else 0
            ),
            "avg_wait_time_low": (
                self.total_wait_time_low / self.total_low_priority
                if self.total_low_priority > 0
                else 0
            ),
        }

    async def update_config(
        self, max_concurrent: int | None = None, reserved_high_priority: int | None = None
    ) -> None:
        """Update semaphore configuration at runtime.

        Args:
            max_concurrent: New max concurrent limit
            reserved_high_priority: New reserved slots for high priority

        """
        async with self.lock:
            if max_concurrent is not None:
                old_max = self.max_concurrent
                self.max_concurrent = max_concurrent
                logger.info(
                    "llm_max_concurrent_updated", old_value=old_max, new_value=max_concurrent
                )

            if reserved_high_priority is not None:
                old_reserved = self.reserved_high_priority
                self.reserved_high_priority = min(reserved_high_priority, self.max_concurrent)
                logger.info(
                    "llm_reserved_high_priority_updated",
                    old_value=old_reserved,
                    new_value=self.reserved_high_priority,
                )

        # Try to grant slots with new config
        await self._try_grant_slots()

    async def clear_waiting_queues(self) -> dict[str, Any]:
        """Clear all waiting tasks from the semaphore queues.

        This is useful when Valkey queues are cleared but semaphore still has
        waiting tasks that will never complete (orphaned waiters).

        WARNING: This can cause deadlock if workers are actively waiting.
        Best practice: Only use this when:
        1. Valkey queues have been cleared, AND
        2. No workers are actively processing, AND
        3. Waiting count is greater than expected

        If unsure, restart the backend instead.

        Returns:
            Dict with counts of cleared tasks

        """
        async with self.lock:
            high_cleared = 0
            low_cleared = 0

            # Drain high-priority queue and set events to None to signal cancellation
            while not self.high_priority_waiters.empty():
                try:
                    event = self.high_priority_waiters.get_nowait()
                    # Set event to wake up any waiting tasks, but they'll see it's cancelled
                    event.set()
                    high_cleared += 1
                except asyncio.QueueEmpty:
                    break

            # Drain low-priority queue and set events to None to signal cancellation
            while not self.low_priority_waiters.empty():
                try:
                    event = self.low_priority_waiters.get_nowait()
                    # Set event to wake up any waiting tasks
                    event.set()
                    low_cleared += 1
                except asyncio.QueueEmpty:
                    break

            # Both queues are now drained, so any tombstones are stale.
            self._abandoned.clear()

            # After clearing, try to grant slots to any remaining waiters
            await self._try_grant_slots()

            logger.warning(
                "semaphore_waiting_queues_cleared",
                high_priority_cleared=high_cleared,
                low_priority_cleared=low_cleared,
                recommendation="restart_backend_if_deadlock_persists",
            )

            return {
                "high_priority_cleared": high_cleared,
                "low_priority_cleared": low_cleared,
                "total_cleared": high_cleared + low_cleared,
            }


# Global singleton instance (shared across all LLM providers). Double-checked
# locking guards first-init from concurrent callers (e.g. cortex startup +
# neuron worker boot).
_global_semaphore: PrioritySemaphore | None = None
_global_semaphore_lock = threading.Lock()


def get_llm_semaphore(
    max_concurrent: int = 1, reserved_high_priority: int = 0
) -> PrioritySemaphore:
    """Get or create the global LLM priority semaphore.

    Args:
        max_concurrent: Max concurrent LLM requests (only used on first call)
        reserved_high_priority: Reserved slots for high priority (only used on first call)

    Returns:
        Global PrioritySemaphore instance

    """
    global _global_semaphore

    if _global_semaphore is None:
        with _global_semaphore_lock:
            if _global_semaphore is None:
                _global_semaphore = PrioritySemaphore(
                    max_concurrent=max_concurrent,
                    reserved_high_priority=reserved_high_priority,
                )

    return _global_semaphore


async def update_llm_semaphore_config(
    max_concurrent: int | None = None, reserved_high_priority: int | None = None
) -> None:
    """Update global semaphore configuration.

    Args:
        max_concurrent: New max concurrent limit
        reserved_high_priority: New reserved slots

    """
    semaphore = get_llm_semaphore()
    await semaphore.update_config(max_concurrent, reserved_high_priority)


async def clear_llm_semaphore_waiting_queues() -> dict[str, Any]:
    """Clear all waiting tasks from the global semaphore queues.

    Useful when Valkey queues are cleared but semaphore still has orphaned waiters.

    Returns:
        Dict with counts of cleared tasks

    """
    semaphore = get_llm_semaphore()
    return await semaphore.clear_waiting_queues()
