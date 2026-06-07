# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for WorkerProbe including the missing-heartbeat grace period."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from chaoscypher_core.services.events.health.probes.worker import (
    _MISSING_HEARTBEAT_GRACE_SECONDS,
    WorkerProbe,
)


def _make_health_fn(value: dict[str | bytes, object]):
    async def _fn() -> dict[str | bytes, object]:
        return value

    return _fn


class TestWorkerProbeOk:
    """Heartbeat present → status is ok."""

    @pytest.mark.asyncio
    async def test_running_with_active_tasks(self) -> None:
        probe = WorkerProbe(
            queue_name="llm",
            probe_name="llm_worker",
            health_fn=_make_health_fn({"running": "2", "queued": "0"}),
        )
        result = await probe.check()
        assert result.status == "ok"
        assert "2 active" in result.message

    @pytest.mark.asyncio
    async def test_running_idle(self) -> None:
        probe = WorkerProbe(
            queue_name="operations",
            probe_name="ops_worker",
            health_fn=_make_health_fn({"running": "0", "queued": "0"}),
        )
        result = await probe.check()
        assert result.status == "ok"
        assert result.message == "Running (idle)"

    @pytest.mark.asyncio
    async def test_bytes_keyed_response(self) -> None:
        """Valkey clients with decode_responses=False return bytes keys."""
        probe = WorkerProbe(
            queue_name="llm",
            probe_name="llm_worker",
            health_fn=_make_health_fn({b"running": b"1"}),
        )
        result = await probe.check()
        assert result.status == "ok"
        assert "1 active" in result.message


class TestWorkerProbeStartupGrace:
    """Heartbeat absent + never seen → warning, not error."""

    @pytest.mark.asyncio
    async def test_empty_response_before_first_observation_is_warning(self) -> None:
        probe = WorkerProbe(
            queue_name="llm",
            probe_name="llm_worker",
            health_fn=_make_health_fn({}),
        )
        result = await probe.check()
        assert result.status == "warning"
        assert "Starting up" in result.message

    @pytest.mark.asyncio
    async def test_missing_health_fn_is_warning(self) -> None:
        probe = WorkerProbe(queue_name="llm", probe_name="llm_worker", health_fn=None)
        result = await probe.check()
        assert result.status == "warning"
        assert "queue unavailable" in result.message.lower()


class TestWorkerProbeLostHeartbeat:
    """After observation, missing heartbeat → warning inside grace, error beyond."""

    @pytest.mark.asyncio
    async def test_brief_gap_after_observation_is_warning(self) -> None:
        state = {"data": {"running": "1"}}

        async def _fn() -> dict[str | bytes, object]:
            return state["data"]

        probe = WorkerProbe(queue_name="llm", probe_name="llm_worker", health_fn=_fn)
        first = await probe.check()
        assert first.status == "ok"

        state["data"] = {}
        # Advance monotonic clock well inside the grace window.
        assert _MISSING_HEARTBEAT_GRACE_SECONDS > 10.0
        with patch(
            "chaoscypher_core.services.events.health.probes.worker.time.monotonic",
            side_effect=lambda: probe._last_seen_at + 10.0,
        ):
            result = await probe.check()
        assert result.status == "warning"
        assert "stale" in result.message.lower()

    @pytest.mark.asyncio
    async def test_long_gap_after_observation_is_error(self) -> None:
        state = {"data": {"running": "1"}}

        async def _fn() -> dict[str | bytes, object]:
            return state["data"]

        probe = WorkerProbe(queue_name="llm", probe_name="llm_worker", health_fn=_fn)
        first = await probe.check()
        assert first.status == "ok"

        state["data"] = {}
        # Advance beyond the grace window.
        with patch(
            "chaoscypher_core.services.events.health.probes.worker.time.monotonic",
            side_effect=lambda: probe._last_seen_at + _MISSING_HEARTBEAT_GRACE_SECONDS + 5.0,
        ):
            result = await probe.check()
        assert result.status == "error"
        assert "no heartbeat" in result.message.lower()


class TestWorkerProbeException:
    """Exception during check → error."""

    @pytest.mark.asyncio
    async def test_check_failure_is_error(self) -> None:
        async def _boom() -> dict[str | bytes, object]:
            raise RuntimeError("valkey timeout")

        probe = WorkerProbe(queue_name="llm", probe_name="llm_worker", health_fn=_boom)
        result = await probe.check()
        assert result.status == "error"
        assert "failed" in result.message.lower()
