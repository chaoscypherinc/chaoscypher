# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for health probe models and registry."""

from __future__ import annotations

import pytest

from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.services.events.health.models import ProbeInfo, ProbeResult
from chaoscypher_core.services.events.health.registry import HealthRegistry


class _FakeProbe:
    """Minimal HealthProbe implementation for testing."""

    def __init__(
        self,
        name: str,
        status: str = "ok",
        message: str = "All good",
        category: str = "resource",
        auto_recoverable: bool = True,
        *,
        raise_on_check: bool = False,
    ) -> None:
        self._name = name
        self._status = status
        self._message = message
        self._category = category
        self._auto_recoverable = auto_recoverable
        self._raise_on_check = raise_on_check

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

    async def check(self) -> ProbeResult:
        """Execute the fake health check."""
        if self._raise_on_check:
            msg = "boom"
            raise RuntimeError(msg)
        return ProbeResult(
            name=self._name,
            status=self._status,
            message=self._message,
            category=self._category,
            auto_recoverable=self._auto_recoverable,
        )


class TestProbeResult:
    """Tests for ProbeResult dataclass."""

    def test_create_ok_result(self) -> None:
        """Verify ProbeResult creation with ok status and default details."""
        from chaoscypher_core.services.events.health.models import ProbeResult

        result = ProbeResult(
            name="disk_space",
            status="ok",
            message="Sufficient disk space available",
            category="resource",
            auto_recoverable=True,
        )
        assert result.name == "disk_space"
        assert result.status == "ok"
        assert result.message == "Sufficient disk space available"
        assert result.category == "resource"
        assert result.auto_recoverable is True
        assert result.details == {}

    def test_create_result_with_details(self) -> None:
        """Verify ProbeResult creation with custom details dict."""
        from chaoscypher_core.services.events.health.models import ProbeResult

        details = {"free_bytes": 1024000, "threshold_bytes": 500000}
        result = ProbeResult(
            name="disk_space",
            status="warning",
            message="Disk space running low",
            category="resource",
            auto_recoverable=True,
            details=details,
        )
        assert result.status == "warning"
        assert result.details["free_bytes"] == 1024000
        assert result.details["threshold_bytes"] == 500000


class TestProbeInfo:
    """Tests for ProbeInfo dataclass."""

    def test_create_probe_info(self) -> None:
        """Verify ProbeInfo creation with all fields."""
        from chaoscypher_core.services.events.health.models import ProbeInfo

        info = ProbeInfo(
            name="llm_provider",
            category="service",
            auto_recoverable=False,
        )
        assert info.name == "llm_provider"
        assert info.category == "service"
        assert info.auto_recoverable is False


class TestHealthRegistry:
    """Tests for HealthRegistry probe management."""

    def test_register_and_list(self) -> None:
        """Register one probe and verify list_probes returns its metadata."""
        registry = HealthRegistry()
        probe = _FakeProbe(name="disk_space", category="resource")

        registry.register(probe)

        infos = registry.list_probes()
        assert len(infos) == 1
        assert infos[0] == ProbeInfo(
            name="disk_space",
            category="resource",
            auto_recoverable=True,
        )

    def test_duplicate_name_raises(self) -> None:
        """Registering two probes with the same name raises ValueError."""
        registry = HealthRegistry()
        registry.register(_FakeProbe(name="dup"))

        with pytest.raises(ValueError, match="Probe already registered: dup"):
            registry.register(_FakeProbe(name="dup"))

    @pytest.mark.asyncio
    async def test_check_all(self) -> None:
        """Run check_all with probes returning different statuses."""
        registry = HealthRegistry()
        registry.register(_FakeProbe(name="ok_probe", status="ok"))
        registry.register(_FakeProbe(name="warn_probe", status="warning"))
        registry.register(
            _FakeProbe(name="boom_probe", raise_on_check=True),
        )

        results = await registry.check_all()

        assert len(results) == 3
        assert results["ok_probe"].status == "ok"
        assert results["warn_probe"].status == "warning"
        assert results["boom_probe"].status == "error"
        assert "exception" in results["boom_probe"].message.lower()

    @pytest.mark.asyncio
    async def test_check_single(self) -> None:
        """Check a single probe by name and verify its result."""
        registry = HealthRegistry()
        registry.register(
            _FakeProbe(name="llm", status="ok", message="Provider healthy"),
        )

        result = await registry.check("llm")

        assert result.name == "llm"
        assert result.status == "ok"
        assert result.message == "Provider healthy"

    @pytest.mark.asyncio
    async def test_check_unknown_raises(self) -> None:
        """Checking a nonexistent probe name raises NotFoundError."""
        registry = HealthRegistry()

        with pytest.raises(NotFoundError) as exc_info:
            await registry.check("ghost")

        assert exc_info.value.resource_type == "Probe"
        assert exc_info.value.identifier == "ghost"

    @pytest.mark.asyncio
    async def test_healthy_flag(self) -> None:
        """All probes returning ok yields healthy=True."""
        registry = HealthRegistry()
        registry.register(_FakeProbe(name="a", status="ok"))
        registry.register(_FakeProbe(name="b", status="ok"))

        _results, healthy = await registry.check_all_with_status()

        assert healthy is True

    @pytest.mark.asyncio
    async def test_unhealthy_when_error(self) -> None:
        """One error probe makes healthy=False."""
        registry = HealthRegistry()
        registry.register(_FakeProbe(name="good", status="ok"))
        registry.register(_FakeProbe(name="bad", status="error"))

        _results, healthy = await registry.check_all_with_status()

        assert healthy is False
