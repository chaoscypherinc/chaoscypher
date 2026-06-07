# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify DatabaseProbe catches a read-only-mounted database."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.events.health.probes.database import DatabaseProbe


@pytest.mark.asyncio
async def test_probe_returns_error_when_writable_check_fails() -> None:
    """A read-only DB returns error even when quick_check passes."""
    adapter = MagicMock()
    adapter.quick_check.return_value = True
    adapter.writable_check.side_effect = RuntimeError("attempt to write a readonly database")

    probe = DatabaseProbe(adapter_fn=lambda: adapter)
    result = await probe.check()

    assert result.status == "error"
    assert (
        "readonly" in result.message.lower()
        or "writable" in result.message.lower()
        or "readonly" in result.message
    )


@pytest.mark.asyncio
async def test_probe_returns_ok_when_writable_check_passes() -> None:
    """A read/write DB returns ok."""
    adapter = MagicMock()
    adapter.quick_check.return_value = True
    adapter.writable_check.return_value = True

    probe = DatabaseProbe(adapter_fn=lambda: adapter)
    result = await probe.check()

    assert result.status == "ok"


@pytest.mark.asyncio
async def test_probe_skips_writable_check_when_quick_check_fails() -> None:
    """A failing quick_check short-circuits — no point trying to write."""
    adapter = MagicMock()
    adapter.quick_check.return_value = False

    probe = DatabaseProbe(adapter_fn=lambda: adapter)
    result = await probe.check()

    assert result.status == "error"
    adapter.writable_check.assert_not_called()
