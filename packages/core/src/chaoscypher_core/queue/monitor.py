# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Queue Monitor.

Handles queue monitoring and statistics.

SRP: Single responsibility for monitoring queue operations.

Example:
    Initialize with queue server connection::

        from chaoscypher_core.queue import QueueMonitor

        monitor = QueueMonitor(client_connection)
        stats = await monitor.get_queue_stats("llm")

"""

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.queue.utils import decode_bytes as _decode_bytes


if TYPE_CHECKING:
    from valkey.asyncio import Valkey

logger = structlog.get_logger(__name__)


class QueueMonitor:
    """Monitors queue operations and provides statistics.

    Responsibilities:
    - Get queue statistics (queued, running, completed, failed)
    - Track token usage and costs
    - Monitor task status
    """

    def __init__(self, client: Valkey | None = None, queues: set | None = None):
        """Initialize queue monitor.

        Args:
            client: Async Valkey connection
            queues: Set of registered queue names

        """
        self.client = client
        self._queues = queues or set()

    async def get_queue_stats(self, queue: str) -> dict[str, Any]:
        """Get statistics for a specific queue.

        Reads from ``queue:{name}:pending`` (sorted set) and
        ``queue:{name}:running`` (set) for live counts, and falls back
        to the ``queue:{name}:health`` hash published by the worker.

        Args:
            queue: Queue name

        Returns:
            Dictionary with queue statistics (queued, running, completed, failed)

        """
        if not self.client:
            return {
                "queue": queue,
                "queued": 0,
                "running": 0,
                "completed_recent": 0,
                "failed_recent": 0,
            }

        # Get queued count from our pending sorted set
        zcard_result = self.client.zcard(f"queue:{queue}:pending")
        queued = (await zcard_result if not isinstance(zcard_result, int) else zcard_result) or 0

        # Get running count from the running set
        scard_result = self.client.scard(f"queue:{queue}:running")
        running = (await scard_result if not isinstance(scard_result, int) else scard_result) or 0

        # Check if worker is alive via health key
        workers = 0
        hgetall_result = self.client.hgetall(f"queue:{queue}:health")
        health_data = (
            await hgetall_result if not isinstance(hgetall_result, dict) else hgetall_result
        )
        if health_data:
            workers = 1
            # If running set is empty but health says running, prefer health data
            health_running = health_data.get(b"running") or health_data.get("running")
            if health_running and running == 0:
                running = int(_decode_bytes(health_running))

        return {
            "queue": queue,
            "queued": queued,
            "running": running,
            "completed_recent": 0,
            "failed_recent": 0,
            "workers": workers,
        }

    async def get_all_stats(self) -> list[dict[str, Any]]:
        """Get statistics for all registered queues.

        Returns:
            List of queue statistics dictionaries

        """
        queues_to_check = self._queues

        if not queues_to_check and self.client:
            # Auto-detect active queues from queue:*:pending keys
            detected_queues: set[str] = set()
            async for key in self.client.scan_iter(match="queue:*:pending"):
                key_str = _decode_bytes(key)
                # Extract queue name from "queue:llm:pending" -> "llm"
                parts = key_str.split(":")
                if len(parts) == 3:
                    detected_queues.add(parts[1])

            # Also check for health keys (worker might be idle with empty queue)
            async for key in self.client.scan_iter(match="queue:*:health"):
                key_str = _decode_bytes(key)
                parts = key_str.split(":")
                if len(parts) == 3:
                    detected_queues.add(parts[1])

            queues_to_check = detected_queues
            logger.debug("queues_auto_detected", queues=detected_queues)

        return list(
            await asyncio.gather(*[self.get_queue_stats(queue) for queue in queues_to_check])
        )

    async def track_tokens(
        self, queue: str, input_tokens: int, output_tokens: int, cost_usd: float = 0.0
    ) -> None:
        """Track token usage and cost for a queue.

        Args:
            queue: Queue name
            input_tokens: Number of input tokens used
            output_tokens: Number of output tokens generated
            cost_usd: Cost in USD

        """
        if not self.client:
            return

        hincrby_result1 = self.client.hincrby(
            f"queue:{queue}:stats", "total_input_tokens", input_tokens
        )
        if not isinstance(hincrby_result1, int):
            await hincrby_result1
        hincrby_result2 = self.client.hincrby(
            f"queue:{queue}:stats", "total_output_tokens", output_tokens
        )
        if not isinstance(hincrby_result2, int):
            await hincrby_result2

        cost_cents = int(cost_usd * 100)
        hincrby_result3 = self.client.hincrby(
            f"queue:{queue}:stats", "total_cost_cents", cost_cents
        )
        if not isinstance(hincrby_result3, int):
            await hincrby_result3

    async def get_token_stats(
        self,
        queue: str,
        custom_input_cost: float = 0.0,
        custom_output_cost: float = 0.0,
    ) -> dict[str, Any]:
        """Get token usage statistics for a queue.

        Args:
            queue: Queue name
            custom_input_cost: Custom cost per million input tokens (overrides stored cost)
            custom_output_cost: Custom cost per million output tokens (overrides stored cost)

        Returns:
            Dictionary with token usage and cost statistics

        """
        if not self.client:
            return {
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
            }

        stats_key = f"queue:{queue}:stats"
        hget_result1 = self.client.hget(stats_key, "total_input_tokens")
        input_tokens_raw = (
            await hget_result1
            if not isinstance(hget_result1, (str, bytes, type(None)))
            else hget_result1
        )
        input_tokens = int(input_tokens_raw or 0)

        hget_result2 = self.client.hget(stats_key, "total_output_tokens")
        output_tokens_raw = (
            await hget_result2
            if not isinstance(hget_result2, (str, bytes, type(None)))
            else hget_result2
        )
        output_tokens = int(output_tokens_raw or 0)

        if custom_input_cost > 0 or custom_output_cost > 0:
            input_cost = (input_tokens / 1_000_000) * custom_input_cost
            output_cost = (output_tokens / 1_000_000) * custom_output_cost
            total_cost_usd = input_cost + output_cost
        else:
            hget_result3 = self.client.hget(stats_key, "total_cost_cents")
            cost_cents_raw = (
                await hget_result3
                if not isinstance(hget_result3, (str, bytes, type(None)))
                else hget_result3
            )
            cost_cents = int(cost_cents_raw or 0)
            total_cost_usd = cost_cents / 100.0

        return {
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "total_cost_usd": total_cost_usd,
        }

    async def clear_token_stats(self, queue: str | None = None) -> None:
        """Clear token statistics.

        Args:
            queue: Optional queue name. If None, clears all queues

        """
        if not self.client:
            return

        if queue:
            await self.client.delete(f"queue:{queue}:stats")
        else:
            for q in self._queues:
                await self.client.delete(f"queue:{q}:stats")

    async def clear_all_stats(self) -> None:
        """Clear all statistics (task history and token stats)."""
        if not self.client:
            return

        await self.client.delete("queue:recent")
        async for key in self.client.scan_iter(match="queue:*:recent"):
            await self.client.delete(key)
        async for key in self.client.scan_iter(match="queue:*:stats"):
            await self.client.delete(key)
