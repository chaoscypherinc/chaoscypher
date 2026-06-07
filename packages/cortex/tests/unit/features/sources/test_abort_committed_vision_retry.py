# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: aborting a COMMITTED source with in-flight vision retry tasks.

User-reported 2026-05-22: clicking Stop after "Retry N failed" on a
committed source's vision panel returned HTTP 400 with "Source is not
currently processing (status: committed)". The per-page OP_VISION_PAGE
tasks kept running to completion despite the explicit cancel.

Root cause: ``abort_processing`` checked ``source.status`` and raised
``RuntimeError`` for any non-processing status, including COMMITTED.
It did not consider that a per-page vision retry leaves source.status
at COMMITTED (vision retries don't flip back to VISION_PENDING) while
PENDING ``vision_page_descriptions`` rows are still in flight.

Fix: before raising the "not currently processing" error, check for
PENDING ``vision_page_descriptions`` rows on a COMMITTED source. If
any exist, cancel OP_VISION_PAGE + OP_VISION_FINALIZE tasks and mark
the pending rows as ``failed`` with ``error_message="aborted by user"``
so the recovery reconciler does not re-enqueue them. ``source.status``
stays COMMITTED — only the in-flight retry is aborted.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.constants import (
    OP_VISION_FINALIZE,
    OP_VISION_PAGE,
    QUEUE_LLM,
    QUEUE_OPERATIONS,
)
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.vision.states import VisionPageStatus
from chaoscypher_cortex.features.sources.service import SourceService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.pagination.default_page_size = 50
    settings.pagination.max_page_size = 1000
    settings.pagination.extraction_tasks_page_size = 25
    settings.batching.template_name_cache_size = 100
    settings.priorities.background = 50
    settings.data_dir = "/tmp/cc-data"
    return settings


def _make_service(adapter: MagicMock) -> SourceService:
    return SourceService(
        engine_service=MagicMock(),
        database_name="default",
        settings=_settings(),
        storage_adapter=adapter,
    )


def _committed_source(source_id: str) -> dict[str, object]:
    return {
        "id": source_id,
        "status": SourceStatus.COMMITTED,
        "current_extraction_job_id": None,
        "filepath": "/data/test.pdf",
        "filename": "test.pdf",
        "file_type": "pdf",
    }


def _pending_vision_page(source_id: str, page_id: str, page_number: int = 1) -> dict[str, object]:
    return {
        "id": page_id,
        "source_id": source_id,
        "vision_job_id": "vjob_test",
        "page_number": page_number,
        "region_index": 0,
        "kind": "pdf_page",
        "status": VisionPageStatus.PENDING.value,
        "description": None,
        "image_path": f"/data/images/{source_id}/page_{page_number}.png",
        "finish_reason": None,
        "error_message": None,
        "attempts": 0,
        "created_at": None,
        "updated_at": None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abort_committed_with_pending_vision_pages_cancels_and_marks_failed() -> None:
    """abort_processing on COMMITTED source with PENDING vision rows succeeds.

    Asserts:
    (a) No RuntimeError is raised (abort returns cleanly).
    (b) OP_VISION_PAGE and OP_VISION_FINALIZE tasks are cancelled.
    (c) Each pending vision_page_descriptions row is marked failed with
        error_message="aborted by user".
    (d) adapter.abort_processing is NOT called (source.status stays COMMITTED).
    """
    source_id = "src_committed_retry"
    page_ids = ["vpd_001", "vpd_002", "vpd_003"]

    adapter = MagicMock()
    adapter.get_file.return_value = _committed_source(source_id)
    adapter.list_vision_page_descriptions.return_value = [
        _pending_vision_page(source_id, pid, i + 1) for i, pid in enumerate(page_ids)
    ]

    service = _make_service(adapter=adapter)
    cancel_mock = AsyncMock()

    with patch(
        "chaoscypher_core.queue.queue_client.cancel_by_metadata",
        cancel_mock,
    ):
        # (a) No exception raised — abort returns cleanly
        await service.abort_processing(source_id)

    # (b) Both vision queues cancelled
    cancel_mock.assert_any_await(
        metadata={"source_id": source_id, "operation_type": OP_VISION_PAGE},
        queue=QUEUE_LLM,
    )
    cancel_mock.assert_any_await(
        metadata={"source_id": source_id, "operation_type": OP_VISION_FINALIZE},
        queue=QUEUE_OPERATIONS,
    )
    assert cancel_mock.await_count == 2

    # (c) Each pending row marked failed with correct error_message
    assert adapter.update_vision_page_description.call_count == len(page_ids)
    for pid in page_ids:
        adapter.update_vision_page_description.assert_any_call(
            page_id=pid,
            new_status=VisionPageStatus.FAILED,
            description=None,
            finish_reason=None,
            error_message="aborted by user",
            expected_current_status=VisionPageStatus.PENDING,
        )

    # (d) source.status left alone — abort_processing adapter method NOT called
    adapter.abort_processing.assert_not_called()


@pytest.mark.asyncio
async def test_abort_committed_no_pending_vision_pages_raises_runtime_error() -> None:
    """abort_processing on COMMITTED source with NO pending vision rows still raises.

    When the source is COMMITTED and has no in-flight vision retry rows,
    the original "not currently processing" RuntimeError must still surface
    so the API returns HTTP 400 as before.
    """
    source_id = "src_committed_clean"

    adapter = MagicMock()
    adapter.get_file.return_value = _committed_source(source_id)
    adapter.list_vision_page_descriptions.return_value = []  # no pending rows

    service = _make_service(adapter=adapter)

    with pytest.raises(RuntimeError) as ei:
        await service.abort_processing(source_id)

    assert "not currently processing" in str(ei.value).lower()
    assert "committed" in str(ei.value).lower()
    adapter.abort_processing.assert_not_called()


@pytest.mark.asyncio
async def test_abort_committed_vision_retry_single_page() -> None:
    """Single PENDING vision row: abort cancels tasks and marks the one row failed."""
    source_id = "src_committed_one_page"
    page_id = "vpd_single"

    adapter = MagicMock()
    adapter.get_file.return_value = _committed_source(source_id)
    adapter.list_vision_page_descriptions.return_value = [_pending_vision_page(source_id, page_id)]

    service = _make_service(adapter=adapter)
    cancel_mock = AsyncMock()

    with patch(
        "chaoscypher_core.queue.queue_client.cancel_by_metadata",
        cancel_mock,
    ):
        await service.abort_processing(source_id)

    assert cancel_mock.await_count == 2
    adapter.update_vision_page_description.assert_called_once_with(
        page_id=page_id,
        new_status=VisionPageStatus.FAILED,
        description=None,
        finish_reason=None,
        error_message="aborted by user",
        expected_current_status=VisionPageStatus.PENDING,
    )
    adapter.abort_processing.assert_not_called()
