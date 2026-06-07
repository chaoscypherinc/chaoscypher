# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Queue (Valkey) health probe.

Checks Valkey queue connectivity by calling an async ping function.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from chaoscypher_core.services.events.health.models import ProbeResult


class QueueProbe:
    """Health probe that checks Valkey queue connectivity.

    Calls an async ping function to verify the queue server is
    reachable. Returns warning if no ping function is available
    (queue client not configured).

    Attributes:
        name: Probe identifier ("queue").
        category: Probe category ("service").
        auto_recoverable: Always True (Valkey may come back online).
    """

    def __init__(
        self,
        ping_fn: Callable[[], Coroutine[Any, Any, Any]] | None = None,
    ) -> None:
        """Initialize the queue probe.

        Args:
            ping_fn: Async callable that pings Valkey, or None if the
                queue client is unavailable.
        """
        self._ping_fn = ping_fn

    @property
    def name(self) -> str:
        """Probe identifier."""
        return "queue"

    @property
    def category(self) -> str:
        """Probe category."""
        return "service"

    @property
    def auto_recoverable(self) -> bool:
        """Whether the issue can resolve without intervention."""
        return True

    async def check(self) -> ProbeResult:
        """Check Valkey queue connectivity.

        Returns:
            ProbeResult with "ok" on successful ping, "warning" if
            no client is available, or "error" on connection failure.
        """
        if not self._ping_fn:
            return ProbeResult(
                name=self.name,
                status="warning",
                message="Queue client not available",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )

        try:
            await self._ping_fn()
            return ProbeResult(
                name=self.name,
                status="ok",
                message="Valkey connected",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )
        except Exception:
            return ProbeResult(
                name=self.name,
                status="error",
                message="Valkey unreachable",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )
