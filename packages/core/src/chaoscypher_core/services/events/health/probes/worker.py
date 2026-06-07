# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Worker health probe.

Checks if a queue worker is alive by reading its Valkey health hash.
The worker publishes to ``queue:{name}:health`` every 2s with 10s TTL.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from typing import Any

from chaoscypher_core.services.events.health.models import ProbeResult


# How long we tolerate a missing heartbeat before it counts as an error.
# The worker publishes every 2s with a 10s key TTL, so a healthy worker
# always has a live key. But startup (container boot, migrations, model
# warmup) and restarts can produce long gaps where the key doesn't exist
# yet. Reporting "error" during those windows lights up red banners in
# the UI for a condition that's actually transient. Up to this threshold
# we return "warning" instead so the UI treats it as "pending" rather
# than "broken".
_MISSING_HEARTBEAT_GRACE_SECONDS = 90.0


class WorkerProbe:
    """Health probe that checks a queue worker's heartbeat.

    Reads the worker's health hash from Valkey to determine if it
    is running and how many tasks are active.

    Startup tolerance: until we've observed a heartbeat at least once,
    or for a short window after losing one, a missing key is reported
    as a warning (``"Starting up — heartbeat pending"``) rather than a
    hard error. That prevents spurious red banners when cortex finishes
    booting before the worker has had a chance to publish — a common
    case during container startup, migrations, or model warmup.

    Attributes:
        name: Probe identifier (e.g. "llm_worker" or "ops_worker").
        category: Probe category ("service").
        auto_recoverable: Always True (workers may restart).
    """

    def __init__(
        self,
        queue_name: str,
        probe_name: str,
        health_fn: Callable[[], Coroutine[Any, Any, dict[str | bytes, Any]]] | None = None,
    ) -> None:
        """Initialize the worker probe.

        Args:
            queue_name: Queue name ("llm" or "operations").
            probe_name: Unique probe name (e.g. "llm_worker", "ops_worker").
            health_fn: Async callable returning the worker health hash
                dict (from Valkey hgetall), or None if queue is unavailable.
        """
        self._queue_name = queue_name
        self._probe_name = probe_name
        self._health_fn = health_fn
        # Monotonic seconds since last observed heartbeat. None means we
        # have never seen one — probe is still in its startup window.
        self._last_seen_at: float | None = None

    @property
    def name(self) -> str:
        """Probe identifier."""
        return self._probe_name

    @property
    def category(self) -> str:
        """Probe category."""
        return "service"

    @property
    def auto_recoverable(self) -> bool:
        """Whether the issue can resolve without intervention."""
        return True

    async def check(self) -> ProbeResult:  # noqa: PLR0911  # probe is a 7-state state machine; splitting fragments the logic
        """Check worker status via its Valkey health key.

        Returns:
            ProbeResult with "ok" if the worker is running, "warning"
            if the queue is unavailable or the worker hasn't published
            a heartbeat recently, or "error" only when a worker that
            was previously alive has been silent beyond the grace
            threshold or the probe itself raised.
        """
        if not self._health_fn:
            return ProbeResult(
                name=self.name,
                status="warning",
                message="Unknown (queue unavailable)",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )

        try:
            health_data = await self._health_fn()
        except Exception:
            return ProbeResult(
                name=self.name,
                status="error",
                message="Worker check failed",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )

        if health_data:
            self._last_seen_at = time.monotonic()
            running = int(health_data.get(b"running", health_data.get("running", 0)))
            if running > 0:
                return ProbeResult(
                    name=self.name,
                    status="ok",
                    message=f"Running ({running} active)",
                    category=self.category,
                    auto_recoverable=self.auto_recoverable,
                    details={"active": running},
                )
            return ProbeResult(
                name=self.name,
                status="ok",
                message="Running (idle)",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
                details={"active": 0},
            )

        # Heartbeat absent. Decide warning vs error based on whether the
        # worker has ever been seen and how long it's been silent.
        if self._last_seen_at is None:
            return ProbeResult(
                name=self.name,
                status="warning",
                message="Starting up — heartbeat pending",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )
        gap_seconds = time.monotonic() - self._last_seen_at
        if gap_seconds < _MISSING_HEARTBEAT_GRACE_SECONDS:
            return ProbeResult(
                name=self.name,
                status="warning",
                message=f"Heartbeat stale ({int(gap_seconds)}s) — worker may be restarting",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )
        return ProbeResult(
            name=self.name,
            status="error",
            message=f"No heartbeat for {int(gap_seconds)}s",
            category=self.category,
            auto_recoverable=self.auto_recoverable,
        )
