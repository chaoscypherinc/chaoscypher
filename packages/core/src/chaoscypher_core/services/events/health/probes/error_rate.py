# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Error rate health probe.

Monitors task failure rates over a sliding window and reports
health status based on configurable thresholds. Used by the
health registry to detect sustained task processing failures.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from chaoscypher_core.services.events.health.models import ProbeResult


logger = structlog.get_logger(__name__)


class ErrorRateProbe:
    """Health probe that monitors task error rates.

    Compares failed tasks against total tasks within a window
    and returns ok/warning/error based on configurable thresholds.

    Attributes:
        name: Probe identifier ("error_rate").
        category: Probe category ("operational").
        auto_recoverable: Always True (error rates can recover).
    """

    def __init__(
        self,
        stats_fn: Callable[[], Coroutine[Any, Any, dict[str, int]]],
        window_size: int = 20,
        warn_threshold: float = 0.5,
        error_threshold: float = 0.8,
    ) -> None:
        """Initialize the error rate probe.

        Args:
            stats_fn: Async callable returning ``{"total": int, "failed": int}``.
            window_size: Minimum tasks required before rate is meaningful.
            warn_threshold: Failure rate at or above which status is "warning".
            error_threshold: Failure rate at or above which status is "error".
        """
        self._stats_fn = stats_fn
        self._window_size = window_size
        self._warn_threshold = warn_threshold
        self._error_threshold = error_threshold

    @property
    def name(self) -> str:
        """Probe identifier."""
        return "error_rate"

    @property
    def category(self) -> str:
        """Probe category."""
        return "operational"

    @property
    def auto_recoverable(self) -> bool:
        """Whether the issue can resolve without intervention."""
        return True

    async def check(self) -> ProbeResult:
        """Execute the error rate health check.

        Returns:
            ProbeResult with status based on current failure rate.
            Returns "ok" when insufficient data is available.
        """
        try:
            stats = await self._stats_fn()
        except Exception as exc:
            logger.warning("error_rate_probe_failed", exc_info=True)
            return ProbeResult(
                name=self.name,
                status="error",
                message=f"Failed to retrieve error stats: {exc}",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )

        total = stats["total"]
        failed = stats["failed"]

        if total < self._window_size:
            return ProbeResult(
                name=self.name,
                status="ok",
                message=f"Insufficient data ({total}/{self._window_size} tasks)",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
                details={
                    "total": total,
                    "failed": failed,
                    "rate": 0.0,
                    "window": self._window_size,
                },
            )

        rate = failed / total

        if rate >= self._error_threshold:
            status = "error"
        elif rate >= self._warn_threshold:
            status = "warning"
        else:
            status = "ok"

        message = f"Error rate {rate:.0%} ({failed}/{total} tasks)"

        return ProbeResult(
            name=self.name,
            status=status,
            message=message,
            category=self.category,
            auto_recoverable=self.auto_recoverable,
            details={
                "total": total,
                "failed": failed,
                "rate": rate,
                "window": self._window_size,
            },
        )
