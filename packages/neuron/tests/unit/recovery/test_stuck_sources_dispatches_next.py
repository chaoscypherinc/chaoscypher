# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: startup recovery's failure-release dispatches the next waiter.

``recover_stuck_sources`` is the third release-without-trigger site of Audit
fix F41's slot-lifetime invariant. Its ``job_status == "failed"`` arm calls
``adapter.fail_extraction``, which sets status=ERROR and so frees the single
extraction slot, and then moved on without dispatching. A source parked by
``mark_extraction_waiting`` has no queue task -- only a row breadcrumb that
``trigger_next_waiting_extraction`` reads -- so nothing else picks it up.

Contract:
    1. A failure-release dispatches the next waiting source exactly once.
    2. The dispatch fires ONCE per pass, not once per failed source: there is
       one extraction slot and ``get_oldest_waiting_extraction`` is LIMIT 1,
       so repeated calls could only re-queue the same waiter.
    3. A pass that resets sources but fails none must not dispatch (no slot
       was released by ``fail_extraction``).
    4. A dispatch failure is logged and suppressed -- recovery still reports
       its counts.
    5. Without ``settings`` the dispatch is skipped and logged, never raised.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from chaoscypher_neuron.recovery.sources import recover_stuck_sources


_TRIGGER = "chaoscypher_neuron.recovery.sources.trigger_next_waiting_extraction"


@pytest.fixture
def mock_adapter():
    return MagicMock()


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.priorities = MagicMock(background=10)
    return settings


@pytest.mark.asyncio
async def test_failure_release_dispatches_next_waiting(mock_adapter) -> None:
    mock_adapter.get_stuck_extracting_sources.return_value = [
        {"id": "src1", "extraction_job_status": "failed"},
    ]
    trigger = AsyncMock()

    with patch(_TRIGGER, new=trigger):
        result = await recover_stuck_sources(mock_adapter, "test_db", settings=_settings())

    assert result == {"reset": 0, "marked_failed": 1}
    mock_adapter.fail_extraction.assert_called_once()
    trigger.assert_awaited_once()
    assert trigger.await_args.args[0] is mock_adapter
    assert trigger.await_args.args[1] == "test_db"


@pytest.mark.asyncio
async def test_dispatch_fires_once_for_several_failed_sources(mock_adapter) -> None:
    """One slot, one LIMIT-1 waiter: a per-source call would double-queue it."""
    mock_adapter.get_stuck_extracting_sources.return_value = [
        {"id": "src1", "extraction_job_status": "failed"},
        {"id": "src2", "extraction_job_status": "failed"},
        {"id": "src3", "extraction_job_status": "failed"},
    ]
    trigger = AsyncMock()

    with patch(_TRIGGER, new=trigger):
        result = await recover_stuck_sources(mock_adapter, "test_db", settings=_settings())

    assert result["marked_failed"] == 3
    assert mock_adapter.fail_extraction.call_count == 3
    trigger.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_only_pass_does_not_dispatch(mock_adapter) -> None:
    """A rewind to ``indexed`` does not go through ``fail_extraction``."""
    mock_adapter.get_stuck_extracting_sources.return_value = [
        {"id": "src1", "extraction_job_status": "running"},
    ]
    trigger = AsyncMock()

    with patch(_TRIGGER, new=trigger):
        result = await recover_stuck_sources(mock_adapter, "test_db", settings=_settings())

    assert result["reset"] == 1
    assert result["marked_failed"] == 0
    trigger.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_stuck_sources_does_not_dispatch(mock_adapter) -> None:
    mock_adapter.get_stuck_extracting_sources.return_value = []
    trigger = AsyncMock()

    with patch(_TRIGGER, new=trigger):
        await recover_stuck_sources(mock_adapter, "test_db", settings=_settings())

    trigger.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_failure_is_logged_and_suppressed(mock_adapter) -> None:
    mock_adapter.get_stuck_extracting_sources.return_value = [
        {"id": "src1", "extraction_job_status": "failed"},
    ]

    async def _boom(*_a: object, **_k: object) -> None:
        raise OSError("dispatch failed")

    with patch(_TRIGGER, new=AsyncMock(side_effect=_boom)), structlog.testing.capture_logs() as cap:
        result = await recover_stuck_sources(mock_adapter, "test_db", settings=_settings())

    assert result["marked_failed"] == 1
    assert "trigger_next_waiting_extraction_failed" in [r["event"] for r in cap]


@pytest.mark.asyncio
async def test_missing_settings_skips_dispatch_and_logs(mock_adapter) -> None:
    """Back-compat: the pre-fix two-argument call must not raise."""
    mock_adapter.get_stuck_extracting_sources.return_value = [
        {"id": "src1", "extraction_job_status": "failed"},
    ]
    trigger = AsyncMock()

    with patch(_TRIGGER, new=trigger), structlog.testing.capture_logs() as cap:
        result = await recover_stuck_sources(mock_adapter, "test_db")

    assert result["marked_failed"] == 1
    trigger.assert_not_awaited()
    assert "recovery_next_extraction_dispatch_skipped" in [r["event"] for r in cap]
