# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: aborting a VISION_PENDING source cancels its vision tasks.

User-reported 2026-05-19: clicking Stop while a source was in vision
processing surfaced "Stop failed: Invalid data provided for
abort_processing". Root cause was twofold:

1. ``processing_statuses`` did not include ``VISION_PENDING``, so the
   guard raised ``RuntimeError`` before any cancellation could happen.
2. The cancellation logic had no branch for vision tasks
   (``OP_VISION_PAGE`` on QUEUE_LLM + ``OP_VISION_FINALIZE`` on
   QUEUE_OPERATIONS).

Also covers the error-envelope fix: the user now sees the actual reason
("Source is not currently processing (status: committed)") instead of
the generic "Invalid data provided" template.
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
from chaoscypher_cortex.features.sources.service import SourceService


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


def _vision_pending_source(source_id: str) -> dict[str, object]:
    return {
        "id": source_id,
        "status": SourceStatus.VISION_PENDING,
        "current_extraction_job_id": None,
        "filepath": "/data/test.pdf",
        "filename": "test.pdf",
        "file_type": "pdf",
    }


def _committed_source(source_id: str) -> dict[str, object]:
    return {
        "id": source_id,
        "status": SourceStatus.COMMITTED,
        "current_extraction_job_id": None,
        "filepath": "/data/test.pdf",
        "filename": "test.pdf",
        "file_type": "pdf",
    }


@pytest.mark.asyncio
async def test_abort_vision_pending_cancels_vision_tasks() -> None:
    """abort_processing on a VISION_PENDING source cancels OP_VISION_PAGE + FINALIZE."""
    source_id = "src_vision"

    adapter = MagicMock()
    adapter.get_file.return_value = _vision_pending_source(source_id)

    service = _make_service(adapter=adapter)
    cancel_mock = AsyncMock()

    with patch(
        "chaoscypher_core.queue.queue_client.cancel_by_metadata",
        cancel_mock,
    ):
        await service.abort_processing(source_id)

    # Both vision queues hit
    cancel_mock.assert_any_await(
        metadata={"source_id": source_id, "operation_type": OP_VISION_PAGE},
        queue=QUEUE_LLM,
    )
    cancel_mock.assert_any_await(
        metadata={"source_id": source_id, "operation_type": OP_VISION_FINALIZE},
        queue=QUEUE_OPERATIONS,
    )
    assert cancel_mock.await_count == 2

    # Source walked to ERROR with the right stage + message
    adapter.abort_processing.assert_called_once()
    kwargs = adapter.abort_processing.call_args.kwargs
    assert kwargs["error_stage"] == "indexing"
    assert kwargs["error_message"] == "Vision processing aborted by user"


@pytest.mark.asyncio
async def test_abort_terminal_status_raises_useful_runtime_error() -> None:
    """Terminal-state source: error message names the actual status, not the validator.

    Source is COMMITTED with no pending vision_page_descriptions rows, so
    abort_processing must still raise RuntimeError (the committed vision-retry
    edge case only fires when PENDING rows exist).
    """
    source_id = "src_committed"

    adapter = MagicMock()
    adapter.get_file.return_value = _committed_source(source_id)
    # No in-flight vision retry rows — the new committed-branch check must
    # find an empty list and fall through to the "not currently processing" error.
    adapter.list_vision_page_descriptions.return_value = []

    service = _make_service(adapter=adapter)

    with pytest.raises(RuntimeError) as ei:
        await service.abort_processing(source_id)

    assert "not currently processing" in str(ei.value).lower()
    assert "committed" in str(ei.value).lower()
    adapter.abort_processing.assert_not_called()


def test_abort_transitions_includes_vision_pending() -> None:
    """_ABORT_TRANSITIONS has a message for VISION_PENDING."""
    from chaoscypher_cortex.features.sources.service import _ABORT_TRANSITIONS

    assert _ABORT_TRANSITIONS[SourceStatus.VISION_PENDING] == "Vision processing aborted by user"


def test_abort_stage_map_routes_vision_pending_to_indexing() -> None:
    """_ABORT_STAGE_MAP routes VISION_PENDING to the indexing error stage.

    Retry resumes the whole indexing pipeline (which re-runs vision as
    part of the indexing → vision_pending → indexing-resume chain).
    """
    from chaoscypher_core.models import SourceErrorStage
    from chaoscypher_cortex.features.sources.service import _ABORT_STAGE_MAP

    assert _ABORT_STAGE_MAP[SourceStatus.VISION_PENDING] == SourceErrorStage.INDEXING
