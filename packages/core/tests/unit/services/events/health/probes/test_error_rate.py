# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ErrorRateProbe."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.services.events.health.models import HealthProbe
from chaoscypher_core.services.events.health.probes.error_rate import ErrorRateProbe


class TestErrorRateProbe:
    """Tests for error rate health probe."""

    @pytest.mark.asyncio
    async def test_ok_when_all_succeed(self) -> None:
        """Zero failures yields ok status."""
        stats_fn = AsyncMock(return_value={"total": 20, "failed": 0})
        probe = ErrorRateProbe(stats_fn)

        result = await probe.check()

        assert result.status == "ok"
        assert result.details["rate"] == 0.0
        assert result.details["total"] == 20
        assert result.details["failed"] == 0

    @pytest.mark.asyncio
    async def test_warning_at_50_percent(self) -> None:
        """Failure rate at 55% triggers warning status."""
        stats_fn = AsyncMock(return_value={"total": 20, "failed": 11})
        probe = ErrorRateProbe(stats_fn)

        result = await probe.check()

        assert result.status == "warning"
        assert result.details["rate"] == 11 / 20
        assert result.details["failed"] == 11

    @pytest.mark.asyncio
    async def test_error_at_80_percent(self) -> None:
        """Failure rate at 85% triggers error status."""
        stats_fn = AsyncMock(return_value={"total": 20, "failed": 17})
        probe = ErrorRateProbe(stats_fn)

        result = await probe.check()

        assert result.status == "error"
        assert result.details["rate"] == 17 / 20
        assert result.details["failed"] == 17

    @pytest.mark.asyncio
    async def test_ok_when_insufficient_data(self) -> None:
        """Below window_size returns ok even when all tasks failed."""
        stats_fn = AsyncMock(return_value={"total": 3, "failed": 3})
        probe = ErrorRateProbe(stats_fn)

        result = await probe.check()

        assert result.status == "ok"
        assert "Insufficient data" in result.message
        assert "3/20" in result.message

    @pytest.mark.asyncio
    async def test_error_on_exception(self) -> None:
        """Exception from stats_fn yields error status."""
        stats_fn = AsyncMock(side_effect=RuntimeError("connection lost"))
        probe = ErrorRateProbe(stats_fn)

        result = await probe.check()

        assert result.status == "error"
        assert "connection lost" in result.message

    def test_protocol_properties(self) -> None:
        """Verify probe satisfies HealthProbe protocol properties."""
        stats_fn = AsyncMock()
        probe = ErrorRateProbe(stats_fn)

        assert probe.name == "error_rate"
        assert probe.category == "operational"
        assert probe.auto_recoverable is True
        assert isinstance(probe, HealthProbe)
