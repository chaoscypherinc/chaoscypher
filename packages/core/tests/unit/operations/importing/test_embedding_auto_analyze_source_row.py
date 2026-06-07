# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""F31: ``auto_analyze`` is read from SourceRow, not the queue payload.

Recovery and re-dispatch paths can rebuild ``file_info`` from a narrower
projection that does not carry every original upload-time flag — most
importantly ``auto_analyze``. Reading from the queue payload silently
broke auto-analysis on those paths: the source landed at INDEXED with no
follow-on extraction and no error to surface in the UI.

The fix: the embedding handler reads ``auto_analyze`` from the SourceRow
(via ``adapter.get_source(source_id, database_name)``) which is the
authoritative store written at upload time and consulted by the source
reconciler.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.operations.importing.embedding_handler import _run_embedding


def _make_indexing_service() -> MagicMock:
    indexing_service = MagicMock()
    indexing_service.settings.embedding.model = "stub-embedding-model"
    indexing_service.settings.search.vector_dimensions = 384
    return indexing_service


def _make_adapter(*, source_row: dict[str, Any] | None) -> MagicMock:
    adapter = MagicMock()
    # No unembedded chunks → _embed_unembedded_chunks returns 0 fast (early-exit
    # keys on the count query), but the rest of the finalize sequence still runs.
    adapter.count_unembedded_chunks.return_value = 0
    adapter.list_unembedded_chunks.return_value = []
    # total_chunks must be > 0 so we don't hit the mid-flight-delete
    # short-circuit in ``_run_embedding`` (aa10d6cd2, 2026-05-22):
    # ``total_chunks == 0`` returns ``status: "deleted"`` BEFORE the
    # ``get_source`` / ``_queue_post_indexing_analysis`` calls these
    # tests are asserting on.
    adapter.get_chunks_by_source.return_value = ([], 1)
    adapter.update_step_progress.return_value = None
    adapter.complete_indexing.return_value = None
    adapter.get_source.return_value = source_row
    return adapter


@pytest.mark.asyncio
async def test_auto_analyze_read_from_source_row_when_payload_missing() -> None:
    """SourceRow says auto_analyze=True but file_info doesn't carry the flag.

    This is the recovery scenario: the queue payload was rebuilt without
    ``auto_analyze``, but the upload-time intent recorded on the row is
    True. Analysis must still be enqueued.
    """
    adapter = _make_adapter(source_row={"id": "src_1", "auto_analyze": True, "filename": "doc.pdf"})
    file_info: dict[str, Any] = {"filename": "doc.pdf"}  # NO auto_analyze key

    settings = MagicMock()
    settings.priorities.background = 50

    with (
        patch(
            "chaoscypher_core.operations.importing.embedding_handler._queue_post_indexing_analysis",
            new=AsyncMock(),
        ) as mock_queue,
        patch("chaoscypher_core.operations.importing.embedding_handler.event_bus"),
    ):
        await _run_embedding(
            source_id="src_1",
            file_info=file_info,
            adapter=adapter,
            indexing_service=_make_indexing_service(),
            settings=settings,
            database_name="default",
            task_id=None,
        )

    adapter.get_source.assert_called_once_with("src_1", "default")
    mock_queue.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_analyze_skipped_when_source_row_says_false() -> None:
    """SourceRow says auto_analyze=False but stale payload says True.

    Trust the row, not the payload. A user who later flipped auto_analyze
    off (or whose row never had it on) must not get analysis enqueued just
    because an old queue task carries a stale True.
    """
    adapter = _make_adapter(
        source_row={"id": "src_1", "auto_analyze": False, "filename": "doc.pdf"}
    )
    file_info: dict[str, Any] = {"filename": "doc.pdf", "auto_analyze": True}

    settings = MagicMock()

    with (
        patch(
            "chaoscypher_core.operations.importing.embedding_handler._queue_post_indexing_analysis",
            new=AsyncMock(),
        ) as mock_queue,
        patch("chaoscypher_core.operations.importing.embedding_handler.event_bus"),
    ):
        await _run_embedding(
            source_id="src_1",
            file_info=file_info,
            adapter=adapter,
            indexing_service=_make_indexing_service(),
            settings=settings,
            database_name="default",
            task_id=None,
        )

    adapter.get_source.assert_called_once_with("src_1", "default")
    mock_queue.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_analyze_falls_back_to_payload_when_source_row_missing() -> None:
    """If the row vanishes, fall back to the payload but log loudly.

    A missing row at this stage is a hard inconsistency (the row was
    loaded earlier in the same handler chain). We still attempt the
    dispatch from the payload so a recoverable transient lookup doesn't
    silently lose auto-analysis, and emit an error log so observability
    catches the inconsistency.
    """
    adapter = _make_adapter(source_row=None)
    file_info: dict[str, Any] = {"filename": "doc.pdf", "auto_analyze": True}

    settings = MagicMock()

    with (
        patch(
            "chaoscypher_core.operations.importing.embedding_handler._queue_post_indexing_analysis",
            new=AsyncMock(),
        ) as mock_queue,
        patch("chaoscypher_core.operations.importing.embedding_handler.event_bus"),
    ):
        await _run_embedding(
            source_id="src_1",
            file_info=file_info,
            adapter=adapter,
            indexing_service=_make_indexing_service(),
            settings=settings,
            database_name="default",
            task_id=None,
        )

    mock_queue.assert_awaited_once()
