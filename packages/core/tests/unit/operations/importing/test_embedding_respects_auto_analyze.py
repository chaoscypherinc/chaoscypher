# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workstream 1: ``auto_analyze=False`` on the row blocks analysis dispatch.

Companion to ``test_embedding_auto_analyze_source_row.py`` (F31). With
the W1 write-side fix in place, the upload row always carries the
user's actual ``auto_analyze`` choice, so the F31 read path now produces
the right behaviour from the first run, not just on recovery.
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
    # keys on the count query).
    adapter.count_unembedded_chunks.return_value = 0
    adapter.list_unembedded_chunks.return_value = []
    # total_chunks must be > 0 so we don't hit the mid-flight-delete
    # short-circuit in ``_run_embedding`` (aa10d6cd2, 2026-05-22).
    adapter.get_chunks_by_source.return_value = ([], 1)
    adapter.update_step_progress.return_value = None
    adapter.complete_indexing.return_value = None
    adapter.get_source.return_value = source_row
    return adapter


@pytest.mark.asyncio
async def test_embedding_does_not_queue_analysis_when_row_auto_analyze_false() -> None:
    """``auto_analyze=False`` on the row blocks the post-index analysis enqueue.

    With the W1 write side fixed, the upload service persists the user's
    choice on the row at upload time. The embedding handler reads that
    value (F31) and skips the analysis dispatch when it is False.
    """
    adapter = _make_adapter(
        source_row={
            "id": "src_no_analysis",
            "auto_analyze": False,
            "filename": "doc.pdf",
        }
    )

    # Empty payload simulates a cold-start enqueue where every user
    # setting now lives on the row, not the payload.
    file_info: dict[str, Any] = {"filename": "doc.pdf"}
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
            source_id="src_no_analysis",
            file_info=file_info,
            adapter=adapter,
            indexing_service=_make_indexing_service(),
            settings=settings,
            database_name="default",
            task_id=None,
        )

    adapter.get_source.assert_called_once_with("src_no_analysis", "default")
    mock_queue.assert_not_awaited()
