# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: a user cancel/abort of an extraction dispatches the next waiter.

Slot-lifetime invariant (Audit fix F41). A source parked by
``mark_extraction_waiting`` has no queue task, only a row breadcrumb, and is
dispatched only when a slot releaser calls ``trigger_next_waiting_extraction``.
Every worker-side release did; "Cancel extraction" and "Stop processing" freed
the slot and dispatched nothing, so a manual-control (``auto_analyze=False``)
waiter stayed at ``indexed`` until some other extraction finished.

The tests drive the real ``trigger_next_waiting_extraction`` and stub only the
enqueue it ends in.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.models import SourceStatus
from chaoscypher_cortex.features.sources.service import SourceService


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.priorities.background = 50
    return settings


def _adapter(status: str) -> MagicMock:
    adapter = MagicMock()
    adapter.get_file.return_value = {
        "id": "src_holder",
        "status": status,
        "current_extraction_job_id": "job_holder",
    }
    adapter.get_oldest_waiting_extraction.return_value = {
        "id": "src_waiter",
        "extraction_queued_at": "2026-10-20T00:00:00Z",
        "extraction_pending_file_info": {"analysis_depth": "quick"},
    }
    return adapter


def _service(adapter: MagicMock) -> SourceService:
    return SourceService(
        engine_service=MagicMock(),
        database_name="default",
        settings=_settings(),
        storage_adapter=adapter,
    )


@pytest.mark.asyncio
async def test_cancel_extraction_dispatches_next_waiting_source() -> None:
    adapter = _adapter(SourceStatus.EXTRACTING)
    enqueue = AsyncMock()

    with (
        patch("chaoscypher_core.queue.queue_client.cancel_by_metadata", new=AsyncMock()),
        patch("chaoscypher_core.operations.queue_utils.queue_import_analysis", new=enqueue),
    ):
        await _service(adapter).cancel_extraction("src_holder")

    adapter.cancel_extraction.assert_called_once_with("src_holder")
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["file_id"] == "src_waiter"
    assert enqueue.await_args.kwargs["analysis_depth"] == "quick"


@pytest.mark.asyncio
async def test_abort_extracting_source_dispatches_next_waiting_source() -> None:
    adapter = _adapter(SourceStatus.EXTRACTING)
    enqueue = AsyncMock()

    with (
        patch("chaoscypher_core.queue.queue_client.cancel_by_metadata", new=AsyncMock()),
        patch("chaoscypher_core.operations.queue_utils.queue_import_analysis", new=enqueue),
    ):
        await _service(adapter).abort_processing("src_holder")

    adapter.abort_processing.assert_called_once()
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["file_id"] == "src_waiter"


@pytest.mark.asyncio
async def test_abort_before_extraction_dispatches_nothing() -> None:
    """An abort that never held the extraction slot frees nothing to hand on."""
    adapter = _adapter(SourceStatus.INDEXING)
    enqueue = AsyncMock()

    with (
        patch("chaoscypher_core.queue.queue_client.cancel_by_metadata", new=AsyncMock()),
        patch("chaoscypher_core.operations.queue_utils.queue_import_analysis", new=enqueue),
    ):
        await _service(adapter).abort_processing("src_holder")

    enqueue.assert_not_awaited()
    adapter.get_oldest_waiting_extraction.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_failure_does_not_fail_the_cancel() -> None:
    adapter = _adapter(SourceStatus.EXTRACTING)
    adapter.get_oldest_waiting_extraction.side_effect = RuntimeError("db busy")

    with patch("chaoscypher_core.queue.queue_client.cancel_by_metadata", new=AsyncMock()):
        await _service(adapter).cancel_extraction("src_holder")

    adapter.cancel_extraction.assert_called_once_with("src_holder")
