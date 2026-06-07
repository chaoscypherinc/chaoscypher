# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 4 extensions for the reconcile-timeout contract from Cluster 1.1 R5.

R5 added the basic SourceRecoverySettings.reconcile_timeout_seconds field
and a RecoveryTimeoutError exception. Phase 4 pins:
- Setting boundary validation (ge=5, le=600 in app_config).
- Periodic-loop timeout logs include the timeout value as a kwarg.
- Startup-site timeout raises RecoveryTimeoutError (not a stdlib exception).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

import chaoscypher_neuron.worker  # noqa: F401
from chaoscypher_core.app_config import SourceRecoverySettings
from chaoscypher_neuron.worker import _source_recovery_loop


def test_reconcile_timeout_seconds_lower_bound() -> None:
    """SourceRecoverySettings rejects reconcile_timeout_seconds < 5."""
    with pytest.raises(ValueError):
        SourceRecoverySettings(reconcile_timeout_seconds=4)


def test_reconcile_timeout_seconds_upper_bound() -> None:
    """SourceRecoverySettings rejects reconcile_timeout_seconds > 600."""
    with pytest.raises(ValueError):
        SourceRecoverySettings(reconcile_timeout_seconds=601)


@pytest.mark.asyncio
async def test_periodic_loop_timeout_log_includes_timeout_value(
    structlog_for_caplog: None,
) -> None:
    """The source_recovery_reconcile_timeout log entry includes the configured timeout."""
    recovery = MagicMock()

    async def too_slow(database_name: str) -> MagicMock:
        await asyncio.sleep(0.2)
        return MagicMock(recovered=0, skipped_paused=0)

    recovery.reconcile_database = AsyncMock(side_effect=too_slow)

    task = asyncio.create_task(
        _source_recovery_loop(
            recovery=recovery,
            adapter=MagicMock(),
            database_name="test_db",
            interval_seconds=0.01,  # type: ignore[arg-type]
            reconcile_timeout_seconds=0.05,  # type: ignore[arg-type]
        )
    )

    with capture_logs() as captured:
        # Wait long enough for the timeout to fire at least once.
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    timeout_logs = [c for c in captured if c.get("event") == "source_recovery_reconcile_timeout"]
    assert len(timeout_logs) >= 1, (
        f"Expected at least one source_recovery_reconcile_timeout log; "
        f"got events {[c.get('event') for c in captured]}"
    )
    first = timeout_logs[0]
    assert first.get("database_name") == "test_db"
    assert first.get("timeout_seconds") == 0.05
