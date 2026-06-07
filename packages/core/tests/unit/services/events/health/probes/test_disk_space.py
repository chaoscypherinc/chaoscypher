# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for disk space health probe."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from chaoscypher_core.services.events.health.models import ProbeResult
from chaoscypher_core.services.events.health.probes.disk_space import DiskSpaceProbe


class _FakeDiskUsage:
    """Minimal stand-in for shutil.disk_usage return value."""

    def __init__(self, free: int) -> None:
        self.total = free * 2
        self.used = free
        self.free = free


class TestDiskSpaceProbe:
    """Tests for DiskSpaceProbe health check."""

    @pytest.mark.asyncio
    async def test_ok_when_plenty_of_space(self) -> None:
        """Return ok status when free space exceeds the warning threshold."""
        probe = DiskSpaceProbe(path="/data")

        with patch("shutil.disk_usage", return_value=_FakeDiskUsage(10 * 1024**3)):
            result = await probe.check()

        assert isinstance(result, ProbeResult)
        assert result.status == "ok"
        assert result.name == "disk_space"
        assert result.details["free_bytes"] == 10 * 1024**3
        assert result.details["path"] == "/data"

    @pytest.mark.asyncio
    async def test_warning_when_low(self) -> None:
        """Return warning status when free space is between error and warn thresholds."""
        probe = DiskSpaceProbe(path="/data")
        free = int(1.5 * 1024**3)  # 1.5 GB

        with patch("shutil.disk_usage", return_value=_FakeDiskUsage(free)):
            result = await probe.check()

        assert result.status == "warning"
        assert result.details["free_bytes"] == free
        assert "free_human" in result.details

    @pytest.mark.asyncio
    async def test_error_when_critical(self) -> None:
        """Return error status when free space drops below the error threshold."""
        probe = DiskSpaceProbe(path="/data")
        free = 500 * 1024**2  # 500 MB

        with patch("shutil.disk_usage", return_value=_FakeDiskUsage(free)):
            result = await probe.check()

        assert result.status == "error"
        assert result.details["free_bytes"] == free

    @pytest.mark.asyncio
    async def test_error_on_exception(self) -> None:
        """Return error status when disk_usage raises OSError."""
        probe = DiskSpaceProbe(path="/nonexistent")

        with patch("shutil.disk_usage", side_effect=OSError("No such path")):
            result = await probe.check()

        assert result.status == "error"
        assert "No such path" in result.message
        assert result.details["path"] == "/nonexistent"

    def test_protocol_properties(self) -> None:
        """Verify probe name, category, and auto_recoverable values."""
        probe = DiskSpaceProbe(path="/data")

        assert probe.name == "disk_space"
        assert probe.category == "resource"
        assert probe.auto_recoverable is False
