# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: manual recover_source bypasses exhaustion + zeros counter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.models import SourceStatus
from chaoscypher_core.services.sources.recovery import SourceRecovery


def _make_service(*, exhausted_source: dict) -> tuple[SourceRecovery, MagicMock, AsyncMock]:
    adapter = MagicMock()
    adapter.get_source.return_value = exhausted_source
    adapter.get_system_state.return_value = {"processing_paused": False}
    adapter.reset_source_recovery_attempts = MagicMock()
    adapter.increment_source_recovery_attempts = MagicMock()
    adapter.mark_source_exhausted = MagicMock()
    adapter.update_source_last_activity = MagicMock()
    adapter.get_active_extraction_job.return_value = None

    queue_client = AsyncMock()
    service = SourceRecovery(
        adapter=adapter,
        queue_client=queue_client,
        max_recovery_attempts=5,
    )
    # Force the classify path to short-circuit by returning None — we're
    # only testing the counter zeroing + exhaustion bypass, not dispatch.
    service._classify = AsyncMock(return_value=None)
    service._is_recently_active = MagicMock(return_value=False)
    return service, adapter, queue_client


@pytest.mark.asyncio
async def test_recover_source_zeroes_recovery_attempts_before_dispatch() -> None:
    """Counter is reset to 0 before _recover_one runs."""
    exhausted = {
        "id": "src1",
        "status": SourceStatus.ERROR,
        "is_paused": False,
        "recovery_attempts": 10,
    }
    service, adapter, _ = _make_service(exhausted_source=exhausted)

    await service.recover_source(source_id="src1", database_name="default")

    adapter.reset_source_recovery_attempts.assert_called_once_with(
        source_id="src1", database_name="default"
    )


@pytest.mark.asyncio
async def test_recover_source_does_not_mark_exhausted_when_attempts_high() -> None:
    """The exhaustion guard is bypassed for manual resume."""
    exhausted = {
        "id": "src1",
        "status": SourceStatus.ERROR,
        "is_paused": False,
        "recovery_attempts": 999,  # well over max_recovery_attempts
    }
    service, adapter, _ = _make_service(exhausted_source=exhausted)

    await service.recover_source(source_id="src1", database_name="default")

    adapter.mark_source_exhausted.assert_not_called()


@pytest.mark.asyncio
async def test_automatic_reconcile_still_marks_exhausted() -> None:
    """Bulk reconcile (respect_stall_threshold=True) keeps the guard."""
    exhausted = {
        "id": "src1",
        "status": SourceStatus.ERROR,
        "is_paused": False,
        "recovery_attempts": 999,
    }
    service, adapter, _ = _make_service(exhausted_source=exhausted)

    from chaoscypher_core.services.sources.recovery import RecoveryStats

    stats = RecoveryStats()
    await service._recover_one(
        exhausted,
        "default",
        stats,
        respect_stall_threshold=True,
    )

    adapter.mark_source_exhausted.assert_called_once()
    assert stats.skipped_exhausted == 1
