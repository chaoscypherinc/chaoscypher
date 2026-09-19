# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for HealthPauseEvaluator hysteresis logic."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.events.health.models import ProbeResult
from chaoscypher_core.services.events.health.pause_evaluator import (
    HealthPauseEvaluator,
    _format_pause_reason,
    _parse_pause_reason,
)
from chaoscypher_core.services.events.health.registry import HealthRegistry


class _StubProbe:
    """Minimal HealthProbe with a mutable status for testing."""

    def __init__(
        self,
        name: str,
        *,
        category: str = "service",
        auto_recoverable: bool = True,
    ) -> None:
        self._name = name
        self._category = category
        self._auto_recoverable = auto_recoverable
        self._status = "ok"

    @property
    def name(self) -> str:
        """Probe name."""
        return self._name

    @property
    def category(self) -> str:
        """Probe category."""
        return self._category

    @property
    def auto_recoverable(self) -> bool:
        """Whether the probe is auto-recoverable."""
        return self._auto_recoverable

    def set_status(self, status: str) -> None:
        """Update the status returned by future check() calls."""
        self._status = status

    async def check(self) -> ProbeResult:
        """Execute the stub health check."""
        return ProbeResult(
            name=self._name,
            status=self._status,
            message=f"Stub: {self._status}",
            category=self._category,
            auto_recoverable=self._auto_recoverable,
        )


def _make_evaluator(
    probes: list[_StubProbe],
    *,
    trip: int = 3,
    clear: int = 3,
) -> tuple[HealthPauseEvaluator, MagicMock]:
    """Build an evaluator with stub probes and a mock adapter.

    Returns:
        Tuple of (evaluator, adapter_mock).
    """
    registry = HealthRegistry()
    for probe in probes:
        registry.register(probe)

    adapter = MagicMock()
    adapter.get_system_state.return_value = {
        "id": 1,
        "processing_paused": False,
        "processing_paused_at": None,
        "processing_paused_reason": None,
        "paused_by": None,
    }

    evaluator = HealthPauseEvaluator(
        registry,
        adapter,
        trip_threshold=trip,
        clear_threshold=clear,
    )
    return evaluator, adapter


class TestHealthPauseEvaluator:
    """Tests for HealthPauseEvaluator trip/clear hysteresis."""

    @pytest.mark.asyncio
    async def test_no_action_when_healthy(self) -> None:
        """All probes ok -- set_system_paused is never called."""
        probe = _StubProbe("healthy_probe")
        evaluator, adapter = _make_evaluator([probe])

        await evaluator.tick()

        adapter.set_system_paused.assert_not_called()

    @pytest.mark.asyncio
    async def test_adapter_calls_run_off_the_event_loop(self) -> None:
        """tick() offloads blocking adapter calls via asyncio.to_thread.

        The health-monitor loop shares the neuron event loop with the
        poller/heartbeat/pubsub tasks, so synchronous SQLite calls must
        never run directly on it.
        """
        import threading

        loop_thread = threading.current_thread()
        call_threads: list[threading.Thread] = []

        def _record_state() -> dict[str, object]:
            call_threads.append(threading.current_thread())
            return {
                "id": 1,
                "processing_paused": False,
                "processing_paused_at": None,
                "processing_paused_reason": None,
                "paused_by": None,
            }

        def _record_pause(**_kwargs: object) -> None:
            call_threads.append(threading.current_thread())

        probe = _StubProbe("blocking")
        probe.set_status("error")
        evaluator, adapter = _make_evaluator([probe], trip=1)
        adapter.get_system_state.side_effect = _record_state
        adapter.set_system_paused.side_effect = _record_pause

        # trip=1 → the first tick reads state AND writes the pause.
        await evaluator.tick()

        adapter.set_system_paused.assert_called_once()
        assert len(call_threads) == 2
        assert all(t is not loop_thread for t in call_threads), (
            "adapter calls must run in a worker thread, not on the event loop"
        )

    @pytest.mark.asyncio
    async def test_trips_after_threshold(self) -> None:
        """Error probe triggers pause after trip_threshold consecutive failures."""
        probe = _StubProbe("flaky")
        probe.set_status("error")
        evaluator, adapter = _make_evaluator([probe], trip=2)

        # First tick: 1 failure, threshold=2 -- no pause yet.
        await evaluator.tick()
        adapter.set_system_paused.assert_not_called()

        # Second tick: 2 failures -- pause triggered.
        await evaluator.tick()
        adapter.set_system_paused.assert_called_once()
        call_kwargs = adapter.set_system_paused.call_args[1]
        assert call_kwargs["is_paused"] is True
        assert "flaky" in call_kwargs["reason"]
        assert call_kwargs["paused_by"] == "health_monitor"

    @pytest.mark.asyncio
    async def test_auto_resumes_transient(self) -> None:
        """Auto-pause from a transient probe clears after clear_threshold passes."""
        probe = _StubProbe("transient", auto_recoverable=True)
        probe.set_status("error")
        evaluator, adapter = _make_evaluator([probe], trip=1, clear=2)

        # Trip on first tick.
        await evaluator.tick()
        adapter.set_system_paused.assert_called_once()
        adapter.reset_mock()

        # Switch adapter to paused state for subsequent ticks.
        adapter.get_system_state.return_value = {
            "id": 1,
            "processing_paused": True,
            "processing_paused_reason": "Auto-paused: transient",
            "processing_paused_at": None,
            "paused_by": "health_monitor",
        }

        # Probe recovers.
        probe.set_status("ok")

        # First clear tick: 1 pass, threshold=2 -- stays paused.
        await evaluator.tick()
        adapter.set_system_paused.assert_not_called()

        # Second clear tick: 2 passes -- auto-resume.
        await evaluator.tick()
        adapter.set_system_paused.assert_called_once()
        call_kwargs = adapter.set_system_paused.call_args[1]
        assert call_kwargs["is_paused"] is False

    @pytest.mark.asyncio
    async def test_no_auto_resume_for_resource(self) -> None:
        """Non-recoverable probe keeps system paused even after recovery."""
        probe = _StubProbe("disk", category="resource", auto_recoverable=False)
        probe.set_status("error")
        evaluator, adapter = _make_evaluator([probe], trip=1, clear=1)

        # Trip.
        await evaluator.tick()
        adapter.reset_mock()

        # Switch adapter to paused state.
        adapter.get_system_state.return_value = {
            "id": 1,
            "processing_paused": True,
            "processing_paused_reason": "Auto-paused: disk",
            "processing_paused_at": None,
            "paused_by": "health_monitor",
        }

        # Probe recovers and passes enough times.
        probe.set_status("ok")
        await evaluator.tick()
        await evaluator.tick()

        # Still paused -- set_system_paused NOT called for resume.
        adapter.set_system_paused.assert_not_called()

    @pytest.mark.asyncio
    async def test_never_touches_user_pause(self) -> None:
        """Evaluator does not modify a user-initiated pause."""
        probe = _StubProbe("svc")
        probe.set_status("error")
        evaluator, adapter = _make_evaluator([probe], trip=1)

        adapter.get_system_state.return_value = {
            "id": 1,
            "processing_paused": True,
            "processing_paused_reason": "Manual maintenance",
            "processing_paused_at": None,
            "paused_by": "user",
        }

        await evaluator.tick()
        await evaluator.tick()

        adapter.set_system_paused.assert_not_called()

    @pytest.mark.asyncio
    async def test_resets_on_intermittent(self) -> None:
        """Intermittent ok resets the failure counter so trip=3 is never reached."""
        probe = _StubProbe("flapper")
        evaluator, adapter = _make_evaluator([probe], trip=3)

        # Sequence: fail, fail, pass (resets counter), fail, fail.
        for status in ["error", "error", "ok", "error", "error"]:
            probe.set_status(status)
            await evaluator.tick()

        adapter.set_system_paused.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_recovers_and_clears_owned_probe_trip(self) -> None:
        """A fresh evaluator (empty in-memory witness) against an
        already-paused adapter re-derives the tripped-probe set from the
        persisted reason and clears the pause once its own probe -- which
        it can observe and is green -- satisfies the clear threshold.

        This is the restart scenario from entry 804: the process that
        caused the pause restarted, losing `_tripped_probes`, but the
        persisted pause state still names the probe that tripped it.
        """
        probe = _StubProbe("disk_space", auto_recoverable=True)
        # Starts "ok" -- simulates the probe having recovered by the time
        # this fresh evaluator instance starts ticking after restart.
        evaluator, adapter = _make_evaluator([probe], clear=1)

        adapter.get_system_state.return_value = {
            "id": 1,
            "processing_paused": True,
            "processing_paused_reason": "Auto-paused: disk_space",
            "processing_paused_at": None,
            "paused_by": "health_monitor",
        }

        await evaluator.tick()

        adapter.set_system_paused.assert_called_once()
        call_kwargs = adapter.set_system_paused.call_args[1]
        assert call_kwargs["is_paused"] is False

    @pytest.mark.asyncio
    async def test_restart_leaves_foreign_trip_alone(self) -> None:
        """A fresh evaluator must never clear a persisted pause whose
        reason names only probes it does not itself register -- e.g. a
        Cortex process restarting into a pause that Neuron's QueueProbe
        caused. Disjoint-probe-set correctness: this evaluator has no way
        to vouch for a probe it cannot check.
        """
        probe = _StubProbe("disk_space", auto_recoverable=True)
        evaluator, adapter = _make_evaluator([probe], clear=1)

        adapter.get_system_state.return_value = {
            "id": 1,
            "processing_paused": True,
            "processing_paused_reason": "Auto-paused: queue",
            "processing_paused_at": None,
            "paused_by": "health_monitor",
        }

        await evaluator.tick()
        await evaluator.tick()

        adapter.set_system_paused.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_leaves_alone_on_partial_ownership(
        self,
        structlog_for_caplog: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A fresh evaluator must NOT clear a pause when it owns only
        SOME of the persisted tripped probes -- e.g. Cortex (DiskSpaceProbe
        only) restarting into a pause that Neuron jointly tripped via
        DiskSpaceProbe *and* QueueProbe. Accepting a partial match would
        let this process authorize a GLOBAL clear while blind to whether
        the probe it can't check (``queue``) is still failing. Partial
        coverage must be treated exactly like zero coverage, and the
        decision must be logged (naming the unowned probe) rather than
        silently doing nothing.
        """
        caplog.set_level(logging.DEBUG)
        probe = _StubProbe("disk_space", auto_recoverable=True)
        evaluator, adapter = _make_evaluator([probe], clear=1)

        adapter.get_system_state.return_value = {
            "id": 1,
            "processing_paused": True,
            "processing_paused_reason": "Auto-paused: disk_space, queue",
            "processing_paused_at": None,
            "paused_by": "health_monitor",
        }

        await evaluator.tick()
        await evaluator.tick()

        adapter.set_system_paused.assert_not_called()
        assert "foreign_auto_pause_trip" in caplog.text
        assert "queue" in caplog.text

    @pytest.mark.asyncio
    async def test_restart_recovers_and_clears_when_own_probes_fully_cover(self) -> None:
        """When this process's own registered probes are a SUPERSET of the
        persisted tripped names (e.g. a Neuron-shaped evaluator that
        registers both DiskSpaceProbe and QueueProbe, matching a pause
        jointly tripped by both), the evaluator can fully vouch for the
        pause and clears it once every persisted probe is green for the
        clear threshold.
        """
        disk_probe = _StubProbe("disk_space", auto_recoverable=True)
        queue_probe = _StubProbe("queue", auto_recoverable=True)
        evaluator, adapter = _make_evaluator([disk_probe, queue_probe], clear=1)

        adapter.get_system_state.return_value = {
            "id": 1,
            "processing_paused": True,
            "processing_paused_reason": "Auto-paused: disk_space, queue",
            "processing_paused_at": None,
            "paused_by": "health_monitor",
        }

        await evaluator.tick()

        adapter.set_system_paused.assert_called_once()
        call_kwargs = adapter.set_system_paused.call_args[1]
        assert call_kwargs["is_paused"] is False

    @pytest.mark.asyncio
    async def test_restart_leaves_pause_alone_on_unparseable_reason(self) -> None:
        """A missing or unparseable persisted reason must never be treated
        as a zero-probe trip. The evaluator leaves the pause alone rather
        than guessing, so a human-readable reason format change (or a
        genuinely missing reason) fails safe instead of auto-clearing.
        """
        probe = _StubProbe("disk_space", auto_recoverable=True)
        evaluator, adapter = _make_evaluator([probe], clear=1)

        adapter.get_system_state.return_value = {
            "id": 1,
            "processing_paused": True,
            "processing_paused_reason": None,
            "processing_paused_at": None,
            "paused_by": "health_monitor",
        }

        await evaluator.tick()

        adapter.set_system_paused.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_leaves_pause_alone_on_corrupted_reason(
        self,
        structlog_for_caplog: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A non-``None`` but corrupted/legacy reason (present, but not
        carrying the ``"Auto-paused: "`` prefix this evaluator writes --
        e.g. left over from a prior reason format, or a manual note that
        happens to be sitting in a ``health_monitor``-attributed row) must
        also fail safe rather than clearing, with a warning logged.
        """
        caplog.set_level(logging.WARNING)
        probe = _StubProbe("disk_space", auto_recoverable=True)
        evaluator, adapter = _make_evaluator([probe], clear=1)

        adapter.get_system_state.return_value = {
            "id": 1,
            "processing_paused": True,
            "processing_paused_reason": "Legacy pause note",
            "processing_paused_at": None,
            "paused_by": "health_monitor",
        }

        await evaluator.tick()

        adapter.set_system_paused.assert_not_called()
        assert "unparseable_auto_pause_reason" in caplog.text

    @pytest.mark.asyncio
    async def test_probe_failing_during_pause_joins_witness_and_blocks_resume(
        self,
    ) -> None:
        """A probe crossing the trip threshold WHILE auto-paused must join
        the witness set (persisted, so a restart re-derives the full set),
        and auto-resume must not fire while it is still erroring — resume
        is a global flip, so resuming on the original probes' recovery
        alone un-pauses into a still-degraded system.
        """
        probe_a = _StubProbe("db")
        probe_b = _StubProbe("queue")
        probe_a.set_status("error")
        evaluator, adapter = _make_evaluator([probe_a, probe_b], trip=2, clear=2)

        # Two ticks: A trips, evaluator pauses with witness {db}.
        await evaluator.tick()
        await evaluator.tick()
        assert evaluator._tripped_probes == {"db"}

        # System now durably paused by health_monitor; A recovers, B fails.
        adapter.get_system_state.return_value = {
            "id": 1,
            "processing_paused": True,
            "processing_paused_reason": "Auto-paused: db",
            "processing_paused_at": None,
            "paused_by": "health_monitor",
        }
        probe_a.set_status("ok")
        probe_b.set_status("error")
        for _ in range(4):
            await evaluator.tick()

        # B joined the witness set and its grown reason was re-persisted...
        assert evaluator._tripped_probes == {"db", "queue"}
        grow_calls = [
            c.kwargs
            for c in adapter.set_system_paused.call_args_list
            if c.kwargs.get("is_paused") is True and "queue" in (c.kwargs.get("reason") or "")
        ]
        assert grow_calls, "grown witness set was never re-persisted"
        assert grow_calls[0]["reason"] == "Auto-paused: db, queue"
        assert grow_calls[0]["expect_paused_by"] == "health_monitor"
        # ...and no resume fired despite A passing clear_threshold.
        resume_calls = [
            c
            for c in adapter.set_system_paused.call_args_list
            if c.kwargs.get("is_paused") is False
        ]
        assert not resume_calls, "auto-resumed while a probe was still erroring"

    @pytest.mark.asyncio
    async def test_trip_write_is_guarded_and_lost_race_keeps_witness_empty(self) -> None:
        """The trip write passes expect_unpaused and honors a lost CAS.

        A manual pause landing between the snapshot read and the write must
        not be relabelled: on rowcount 0 the evaluator records nothing.
        """
        probe = _StubProbe("db")
        probe.set_status("error")
        evaluator, adapter = _make_evaluator([probe], trip=1)
        adapter.set_system_paused.return_value = 0  # lost the CAS

        await evaluator.tick()

        call_kwargs = adapter.set_system_paused.call_args.kwargs
        assert call_kwargs["expect_unpaused"] is True
        assert evaluator._tripped_probes == set()

    @pytest.mark.asyncio
    async def test_resume_write_is_guarded_and_lost_race_keeps_witness(self) -> None:
        """The clear write passes expect_paused_by and honors a lost CAS."""
        probe = _StubProbe("disk_space", auto_recoverable=True)
        evaluator, adapter = _make_evaluator([probe], clear=1)
        adapter.get_system_state.return_value = {
            "id": 1,
            "processing_paused": True,
            "processing_paused_reason": "Auto-paused: disk_space",
            "processing_paused_at": None,
            "paused_by": "health_monitor",
        }
        adapter.set_system_paused.return_value = 0  # user re-took the pause

        await evaluator.tick()

        call_kwargs = adapter.set_system_paused.call_args.kwargs
        assert call_kwargs["is_paused"] is False
        assert call_kwargs["expect_paused_by"] == "health_monitor"
        # Witness survives so the next tick re-evaluates from fresh state.
        assert evaluator._tripped_probes == {"disk_space"}


class TestPauseReasonFormatParse:
    """Round-trip contract between the auto-pause reason writer and the
    restart-recovery parser -- both must agree on the same format so a
    process restart can re-derive the tripped-probe set (see entry 804).
    """

    def test_round_trip_single_probe(self) -> None:
        """A single probe name formats and parses back losslessly."""
        reason = _format_pause_reason({"disk_space"})
        assert reason == "Auto-paused: disk_space"
        assert _parse_pause_reason(reason) == {"disk_space"}

    def test_round_trip_multiple_probes_sorted(self) -> None:
        """Multiple probe names are sorted, comma-joined, and round-trip
        regardless of the input set's iteration order.
        """
        reason = _format_pause_reason({"queue", "disk_space"})
        assert reason == "Auto-paused: disk_space, queue"
        assert _parse_pause_reason(reason) == {"disk_space", "queue"}

    def test_parse_returns_none_for_missing_reason(self) -> None:
        """A ``None`` reason is unparseable, not a zero-probe trip."""
        assert _parse_pause_reason(None) is None

    def test_parse_returns_none_for_wrong_prefix(self) -> None:
        """A reason without the auto-pause prefix (e.g. a manual-pause
        message) is never treated as parseable probe data.
        """
        assert _parse_pause_reason("Manual maintenance") is None

    def test_parse_returns_none_for_empty_probe_list(self) -> None:
        """A reason carrying the auto-pause prefix but no probe names
        after it must fail safe rather than parsing to an empty set.
        """
        assert _parse_pause_reason("Auto-paused: ") is None


class TestForeignWitnessResumeGuard:
    """Cortex must not resume a pause whose reason names a probe it lacks."""

    @pytest.mark.asyncio
    async def test_evaluator_with_weaker_registry_declines_to_resume(self) -> None:
        """The shipped registries genuinely diverge.

        The Neuron registers DiskSpaceProbe AND QueueProbe; Cortex registers
        DiskSpaceProbe only. Both run an evaluator against the same singleton
        row. ``_recover_tripped_probes`` refuses partial coverage — but it is
        consulted only when the in-memory witness is EMPTY, so once Cortex
        holds a witness of its own, a later witness-growth by the Neuron is
        invisible to it. Cortex would then lift the system-wide pause on its
        stale witness, logging ``auto_resumed`` while the queue probe is
        still failing.
        """
        disk = _StubProbe("disk")
        disk.set_status("error")
        evaluator, adapter = _make_evaluator([disk], trip=2, clear=2)

        # Disk trips; this evaluator pauses with witness {disk}.
        await evaluator.tick()
        await evaluator.tick()
        assert evaluator._tripped_probes == {"disk"}

        # The sibling process (with the wider registry) grew the persisted
        # reason to include a probe this evaluator cannot see, then disk
        # recovered here.
        adapter.get_system_state.return_value = {
            "id": 1,
            "processing_paused": True,
            "processing_paused_at": None,
            "processing_paused_reason": "Auto-paused: disk, queue",
            "paused_by": "health_monitor",
        }
        disk.set_status("ok")
        adapter.set_system_paused.reset_mock()

        await evaluator.tick()
        await evaluator.tick()
        await evaluator.tick()

        resume_calls = [
            c
            for c in adapter.set_system_paused.call_args_list
            if c.kwargs.get("is_paused") is False
        ]
        assert resume_calls == [], "resumed a pause whose reason it could not cover"
