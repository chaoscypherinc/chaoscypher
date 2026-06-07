# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for HealthPauseEvaluator hysteresis logic."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.events.health.models import ProbeResult
from chaoscypher_core.services.events.health.pause_evaluator import HealthPauseEvaluator
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
