# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for DatabaseProbe."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.events.health.models import HealthProbe
from chaoscypher_core.services.events.health.probes.database import DatabaseProbe


class TestDatabaseProbe:
    """Tests for database health probe."""

    @pytest.mark.asyncio
    async def test_ok_when_healthy(self) -> None:
        """Healthy adapter quick_check + writable_check returns ok status."""
        adapter = MagicMock()
        adapter.quick_check.return_value = True
        adapter.writable_check.return_value = True
        adapter_fn = MagicMock(return_value=adapter)

        probe = DatabaseProbe(adapter_fn)
        result = await probe.check()

        assert result.status == "ok"
        assert result.message == "Database accessible and writable"
        adapter_fn.assert_called_once()
        adapter.quick_check.assert_called_once()
        adapter.writable_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_when_check_fails(self) -> None:
        """Failed quick_check returns error status."""
        adapter = MagicMock()
        adapter.quick_check.return_value = False
        adapter_fn = MagicMock(return_value=adapter)

        probe = DatabaseProbe(adapter_fn)
        result = await probe.check()

        assert result.status == "error"
        assert result.message == "Database unreachable"

    @pytest.mark.asyncio
    async def test_error_on_exception(self) -> None:
        """Exception from adapter_fn yields error status."""
        adapter_fn = MagicMock(side_effect=RuntimeError("db locked"))

        probe = DatabaseProbe(adapter_fn)
        result = await probe.check()

        assert result.status == "error"
        assert "db locked" in result.message

    def test_protocol_properties(self) -> None:
        """Verify probe satisfies HealthProbe protocol properties."""
        adapter_fn = MagicMock()
        probe = DatabaseProbe(adapter_fn)

        assert probe.name == "database"
        assert probe.category == "resource"
        assert probe.auto_recoverable is True
        assert isinstance(probe, HealthProbe)
