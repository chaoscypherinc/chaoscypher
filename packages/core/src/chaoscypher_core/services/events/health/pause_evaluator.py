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

import asyncio
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.services.events.bus import event_bus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.services.events.health.registry import HealthRegistry

logger = structlog.get_logger(__name__)

# Shared format between the trip writer (_format_pause_reason, used when
# persisting a new auto-pause) and the restart-recovery parser
# (_parse_pause_reason, used to re-derive the tripped-probe witness set
# after a process restart loses its in-memory state). Keeping both sides
# of this contract on one prefix constant and one pair of helpers means
# the writer and parser cannot drift independently -- see entry 804.
_AUTO_PAUSE_REASON_PREFIX = "Auto-paused: "


def _format_pause_reason(probe_names: set[str]) -> str:
    """Format tripped probe names into the persisted auto-pause reason.

    Sorts and comma-joins the names after ``_AUTO_PAUSE_REASON_PREFIX`` so
    the output is deterministic and round-trips through
    :func:`_parse_pause_reason`. This is the single source of truth for
    the reason-string format; ``tick()`` never builds this string itself.
    """
    return f"{_AUTO_PAUSE_REASON_PREFIX}{', '.join(sorted(probe_names))}"


def _parse_pause_reason(reason: str | None) -> set[str] | None:
    """Parse probe names back out of a persisted auto-pause reason string.

    Inverse of :func:`_format_pause_reason`. Returns ``None`` -- never an
    empty set -- when ``reason`` is missing, doesn't carry the expected
    prefix, or decodes to no probe names, so callers can distinguish "this
    isn't an auto-pause reason we understand" from "the pause legitimately
    involves zero probes" (which should never happen). Callers must treat
    ``None`` as unrecoverable and leave the pause alone rather than
    guessing.
    """
    if reason is None or not reason.startswith(_AUTO_PAUSE_REASON_PREFIX):
        return None
    names_part = reason[len(_AUTO_PAUSE_REASON_PREFIX) :]
    names = {name.strip() for name in names_part.split(",") if name.strip()}
    return names or None


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

    def _recover_tripped_probes(
        self,
        *,
        reason: str | None,
        own_probes: set[str],
    ) -> set[str]:
        """Re-derive the tripped-probe witness set after a process restart.

        ``_tripped_probes`` lives only in memory, but the pause it guards
        is persisted, so a restarted process starts with an empty set even
        though the system is still paused. This recovers it by parsing the
        persisted ``reason`` string (see :func:`_parse_pause_reason`) and
        requiring ``own_probes`` -- the probes this process's own registry
        actually checks -- to FULLY cover the persisted names.

        Full coverage, not mere overlap, is required: the caller's
        eventual clear-check only iterates the *returned* set, but
        ``set_system_paused(is_paused=False)`` is a global flip. If this
        process only vouches for some of the probes that caused the pause
        (e.g. it owns ``disk_space`` but the pause was jointly caused by
        ``disk_space`` *and* ``queue``, which this process cannot check),
        accepting a partial match would let it clear a system-wide pause
        while blind to whether the probe it can't see is still failing.
        Partial coverage is therefore treated exactly like zero coverage.

        Returns an empty set (never clears this tick) if the reason is
        unparseable, or if ``own_probes`` does not fully cover the
        persisted names (including the case of no overlap at all).

        Args:
            reason: The persisted ``processing_paused_reason`` string.
            own_probes: Names of probes registered on this evaluator's
                own registry (i.e. this tick's probe-result keys).
        """
        parsed = _parse_pause_reason(reason)
        if parsed is None:
            logger.warning("unparseable_auto_pause_reason", reason=reason)
            return set()

        if not parsed <= own_probes:
            logger.debug(
                "foreign_auto_pause_trip",
                persisted=sorted(parsed),
                own_probes=sorted(own_probes),
                unowned=sorted(parsed - own_probes),
            )
            return set()

        return parsed

    async def tick(self) -> None:
        """Run one evaluation cycle.

        Checks all probes, updates hysteresis counters, and triggers
        or clears system pause as appropriate. Manual (user-initiated)
        pauses are never modified.
        """
        results = await self._registry.check_all()
        # Offload the blocking SQLite call; to_thread's context copy carries
        # the caller's session scope into the worker thread.
        state = await asyncio.to_thread(self._adapter.get_system_state)

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
                reason = _format_pause_reason(tripped)
                await asyncio.to_thread(
                    self._adapter.set_system_paused,
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
        if paused_by == "health_monitor" and not self._tripped_probes:
            # Fresh evaluator instance (e.g. after a process restart) has
            # no in-memory witness even though the persisted state says a
            # health-monitor pause is active. Re-derive it from the
            # persisted reason, requiring the probes this process itself
            # registered to FULLY cover the persisted names -- see
            # _recover_tripped_probes for why partial coverage is refused.
            self._tripped_probes = self._recover_tripped_probes(
                reason=state.get("processing_paused_reason"),
                own_probes=set(results),
            )

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
                await asyncio.to_thread(
                    self._adapter.set_system_paused,
                    is_paused=False,
                    reason=None,
                    paused_by=None,
                )
                logger.info(
                    "auto_resumed",
                    probes=sorted(self._tripped_probes),
                )
                self._tripped_probes.clear()
