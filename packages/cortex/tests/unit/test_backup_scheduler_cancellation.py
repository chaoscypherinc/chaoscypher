# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify _backup_scheduler logs a breadcrumb and re-raises on cancellation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs

from chaoscypher_cortex.lifespan import _backup_scheduler


@pytest.mark.asyncio
async def test_backup_scheduler_cancelled_during_initial_sleep_logs_and_reraises(
    tmp_path: Path,
) -> None:
    """A cancel during the initial sleep before the loop must log + re-raise."""
    settings = MagicMock()
    settings.paths.data_dir = str(tmp_path)
    settings.backup.interval = "daily"
    settings.backup.enabled = False

    async def cancelled_sleep(seconds: float) -> None:
        raise asyncio.CancelledError

    with (
        patch("chaoscypher_cortex.lifespan.asyncio.sleep", new=cancelled_sleep),
        capture_logs() as captured,
    ):
        task = asyncio.create_task(_backup_scheduler(settings))
        with pytest.raises(asyncio.CancelledError):
            await task

    events = [c.get("event") for c in captured]
    # The breadcrumb event name is "backup_scheduler_cancelled_during_initial_sleep"
    assert any("backup_scheduler_cancelled" in e for e in events if e), (
        f"Expected a backup_scheduler_cancelled* log breadcrumb; got events: {events}"
    )


@pytest.mark.asyncio
async def test_backup_scheduler_cancelled_during_loop_sleep_logs_and_reraises(
    tmp_path: Path,
) -> None:
    """A cancel during the per-iteration sleep inside the loop must log + re-raise."""
    settings = MagicMock()
    settings.paths.data_dir = str(tmp_path)
    settings.backup.interval = "daily"
    settings.backup.enabled = False

    sleep_calls: dict[str, int] = {"count": 0}

    async def first_sleep_succeeds_then_cancel(seconds: float) -> None:
        sleep_calls["count"] += 1
        if sleep_calls["count"] >= 2:
            raise asyncio.CancelledError
        # First sleep returns normally → enters loop.

    with (
        patch("chaoscypher_cortex.lifespan.asyncio.sleep", new=first_sleep_succeeds_then_cancel),
        capture_logs() as captured,
    ):
        task = asyncio.create_task(_backup_scheduler(settings))
        with pytest.raises(asyncio.CancelledError):
            await task

    events = [c.get("event") for c in captured]
    assert any("backup_scheduler_cancelled" in e for e in events if e), (
        f"Expected a backup_scheduler_cancelled* log breadcrumb; got events: {events}"
    )
