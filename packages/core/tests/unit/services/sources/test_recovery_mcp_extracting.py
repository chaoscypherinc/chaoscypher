# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Recovery coverage for ``mcp_extracting`` sources.

An MCP extraction is driven by an external MCP client submitting chunk
results — there is no queue task for the reconciler to re-dispatch, so a
fresh ``mcp_extracting`` source is an explicit no-op (mirroring
``awaiting_confirmation``). BUT a client that disconnects mid-extraction
leaves the source stalled forever with no operator-visible reason. After
a configurable staleness window with no activity the reconciler marks
the source failed with a clear reason string.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.services.sources.recovery import SourceRecovery


def _seed_mcp_extracting(adapter, *, source_id: str, activity_age_hours: float | None) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "mcp_extracting",
        }
    )
    if activity_age_hours is not None:
        adapter.update_source_last_activity(
            source_id=source_id,
            database_name=adapter.database_name,
            at_time=datetime.now(UTC) - timedelta(hours=activity_age_hours),
        )


def _recovery(adapter, queue, **kwargs) -> SourceRecovery:
    return SourceRecovery(adapter=adapter, queue_client=queue, **kwargs)


def test_mcp_extracting_is_scanned_by_bulk_reconcile() -> None:
    """mcp_extracting must be in NON_TERMINAL_STATUSES so the scan reaches it."""
    assert "mcp_extracting" in SourceRecovery.NON_TERMINAL_STATUSES


@pytest.mark.asyncio
async def test_fresh_mcp_extracting_is_left_alone(in_memory_adapter) -> None:
    """_classify returns None for a recently-active MCP extraction."""
    _seed_mcp_extracting(in_memory_adapter, source_id="src-mcp-fresh", activity_age_hours=0.5)
    queue = AsyncMock()
    recovery = _recovery(in_memory_adapter, queue)

    source = in_memory_adapter.get_source("src-mcp-fresh", database_name="default")
    action = await recovery._classify(source, database_name="default")

    assert action is None, "fresh mcp_extracting must classify as a no-op"
    refreshed = in_memory_adapter.get_source("src-mcp-fresh", database_name="default")
    assert refreshed["status"] == "mcp_extracting"


@pytest.mark.asyncio
async def test_stale_mcp_extracting_marked_failed_with_reason(in_memory_adapter) -> None:
    """No activity beyond the window → status=error with an abandonment reason."""
    _seed_mcp_extracting(in_memory_adapter, source_id="src-mcp-stale", activity_age_hours=7)
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    recovery = _recovery(in_memory_adapter, queue)  # default window: 6 hours

    stats = await recovery.reconcile_database(database_name="default")

    refreshed = in_memory_adapter.get_source("src-mcp-stale", database_name="default")
    assert refreshed["status"] == "error"
    assert "MCP extraction abandoned" in (refreshed.get("error_message") or "")
    assert "6" in (refreshed.get("error_message") or ""), (
        "reason string should name the configured staleness window"
    )
    queue.enqueue.assert_not_awaited()
    assert stats.marked_failed == 1
    assert stats.recovered == 0


@pytest.mark.asyncio
async def test_stale_mark_records_recovery_event(in_memory_adapter) -> None:
    """The abandonment mark lands in the recovery audit trail."""
    _seed_mcp_extracting(in_memory_adapter, source_id="src-mcp-audit", activity_age_hours=8)
    queue = AsyncMock()
    recovery = _recovery(in_memory_adapter, queue)

    await recovery.reconcile_database(database_name="default")

    events = in_memory_adapter.list_recovery_events(
        source_id="src-mcp-audit", database_name="default"
    )
    assert len(events) == 1
    assert events[0]["action_taken"] == "mark_failed"
    assert events[0]["from_status"] == "mcp_extracting"
    assert events[0]["enqueued_count"] == 0


@pytest.mark.asyncio
async def test_staleness_window_honors_setting(in_memory_adapter) -> None:
    """The window is configurable — 2h-old activity is stale at 1h, fresh at 6h."""
    _seed_mcp_extracting(in_memory_adapter, source_id="src-mcp-window", activity_age_hours=2)
    queue = AsyncMock()

    # Wide window: untouched.
    wide = _recovery(in_memory_adapter, queue, mcp_extracting_stale_after_hours=6)
    await wide.reconcile_database(database_name="default")
    assert (
        in_memory_adapter.get_source("src-mcp-window", database_name="default")["status"]
        == "mcp_extracting"
    )

    # Tight window: marked failed.
    tight = _recovery(in_memory_adapter, queue, mcp_extracting_stale_after_hours=1)
    await tight.reconcile_database(database_name="default")
    refreshed = in_memory_adapter.get_source("src-mcp-window", database_name="default")
    assert refreshed["status"] == "error"
    assert "1" in (refreshed.get("error_message") or "")


def test_settings_expose_mcp_extracting_stale_after_hours() -> None:
    """SourceRecoverySettings carries the window (CC046: no hardcoded timing)."""
    from chaoscypher_core.app_config import SourceRecoverySettings

    settings = SourceRecoverySettings()
    assert settings.mcp_extracting_stale_after_hours == 6
