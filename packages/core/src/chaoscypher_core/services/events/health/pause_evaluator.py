# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Health Pause Evaluator.

Tracks consecutive probe failures and triggers or clears system pause
using hysteresis thresholds. The evaluator never overrides a manual
(user-initiated) pause.

Example:
    from chaoscypher_core.services.events.health import HealthPauseEvaluator

    evaluator = HealthPauseEvaluator(registry, adapter, trip_threshold=3)
    await evaluator.tick()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.services.events.bus import event_bus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.services.events.health.registry import HealthRegistry

logger = structlog.get_logger(__name__)


class HealthPauseEvaluator:
    """Auto-pause consumer with hysteresis-based trip/clear logic.

    Monitors probe results from the health registry and pauses the
    system when consecutive failures exceed the trip threshold. When
    all tripped probes recover for enough consecutive passes the
    system is automatically resumed, unless any tripped probe is
    non-recoverable.

    Attributes:
        trip_threshold: Consecutive failures required to trigger a pause.
        clear_threshold: Consecutive passes required to clear an auto-pause.
    """

    def __init__(
        self,
        registry: HealthRegistry,
        adapter: SqliteAdapter,
        trip_threshold: int = 3,
        clear_threshold: int = 3,
    ) -> None:
        """Initialize the pause evaluator.

        Args:
            registry: Health registry containing all registered probes.
            adapter: Storage adapter with get_system_state / set_system_paused.
            trip_threshold: Consecutive failures before auto-pause triggers.
            clear_threshold: Consecutive passes before auto-pause clears.
        """
        self._registry = registry
        self._adapter = adapter
        self.trip_threshold = trip_threshold
        self.clear_threshold = clear_threshold

        self._consecutive_failures: dict[str, int] = {}
        self._consecutive_passes: dict[str, int] = {}
        self._tripped_probes: set[str] = set()
        self._previous_failures: dict[str, int] = {}

    async def tick(self) -> None:
        """Run one evaluation cycle.

        Checks all probes, updates hysteresis counters, and triggers
        or clears system pause as appropriate. Manual (user-initiated)
        pauses are never modified.
        """
        results = await self._registry.check_all()
        state = self._adapter.get_system_state()

        is_paused = state["processing_paused"]
        paused_by = state.get("paused_by")

        # Never touch a manual pause.
        if is_paused and paused_by == "user":
            logger.debug("skip_user_pause")
            return

        # Update per-probe hysteresis counters.
        for name, result in results.items():
            if result.status == "error":
                self._consecutive_failures[name] = self._consecutive_failures.get(name, 0) + 1
                self._consecutive_passes[name] = 0
            else:
                self._consecutive_passes[name] = self._consecutive_passes.get(name, 0) + 1
                self._consecutive_failures[name] = 0

        # Record health_change events on state transitions so the
        # system_events log captures per-probe degradations and
        # recoveries independently of the aggregate trip/clear logic.
        for name, result in results.items():
            if self._consecutive_failures[name] == 1 and name not in self._tripped_probes:
                event_bus.emit(
                    "health_change",
                    action=f"Probe {name} degraded: {result.message}",
                    source="health_monitor",
                    details={
                        "probe": name,
                        "status": result.status,
                        "category": result.category,
                    },
                )
            elif self._consecutive_passes[name] == 1 and self._previous_failures.get(name, 0) > 0:
                event_bus.emit(
                    "health_change",
                    action=f"Probe {name} recovered",
                    source="health_monitor",
                    details={
                        "probe": name,
                        "status": result.status,
                    },
                )

        # Snapshot failure counts so the NEXT tick can detect transitions.
        self._previous_failures = dict(self._consecutive_failures)

        # Trip check: trigger auto-pause when any probe exceeds the threshold.
        if not is_paused:
            tripped = {
                name
                for name, count in self._consecutive_failures.items()
                if count >= self.trip_threshold
            }
            if tripped:
                self._tripped_probes = tripped
                probe_list = ", ".join(sorted(tripped))
                reason = f"Auto-paused: {probe_list}"
                self._adapter.set_system_paused(
                    is_paused=True,
                    reason=reason,
                    paused_by="health_monitor",
                )
                logger.info(
                    "auto_paused",
                    probes=sorted(tripped),
                    reason=reason,
                )
            return

        # Clear check: auto-resume only if we caused the pause.
        if paused_by == "health_monitor" and self._tripped_probes:
            # Cannot auto-resume if any tripped probe is non-recoverable.
            for name in self._tripped_probes:
                tripped_result = results.get(name)
                if tripped_result and not tripped_result.auto_recoverable:
                    logger.debug(
                        "skip_non_recoverable",
                        probe=name,
                    )
                    return

            # All tripped probes must pass consecutively.
            all_cleared = all(
                self._consecutive_passes.get(name, 0) >= self.clear_threshold
                for name in self._tripped_probes
            )
            if all_cleared:
                self._adapter.set_system_paused(
                    is_paused=False,
                    reason=None,
                    paused_by=None,
                )
                logger.info(
                    "auto_resumed",
                    probes=sorted(self._tripped_probes),
                )
                self._tripped_probes.clear()
