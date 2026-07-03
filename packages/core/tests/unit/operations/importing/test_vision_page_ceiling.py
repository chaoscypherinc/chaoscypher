# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""_apply_vision_processing enforces the per-source vision-page ceiling.

Cost / resource-exhaustion fix (2026-05-25 review pass 2): full-mode vision
enqueued one OP_VISION_PAGE LLM task per image page with no per-document
ceiling. A pathological multi-thousand-page PDF could explode into thousands
of vision-LLM calls. Full mode now hard-fails the source (zero vision tasks
created) when the image-page count exceeds ``loader.vision_max_pages``. Quick
mode is unaffected — it already samples down to vision_quick_sample_max_pages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import SourceFanoutLimitExceededError
from chaoscypher_core.operations.importing import indexing_handler


def _documents_with_pages(n: int) -> list[dict[str, Any]]:
    return [
        {
            "content": "...",
            "metadata": {
                "pages": [{"page_number": i + 1, "has_images": True} for i in range(n)],
            },
        }
    ]


def _engine_settings(
    tmp_path: Path, *, vision_max_pages: int, vision_quick_sample_max_pages: int = 20
) -> Any:
    engine_settings = MagicMock()
    engine_settings.loader.vision_max_pages = vision_max_pages
    engine_settings.loader.vision_quick_sample_max_pages = vision_quick_sample_max_pages
    # Pin the MagicMock's data_dir so any Path(engine_settings.paths.data_dir)
    # construction lands inside tmp_path instead of stringifying the mock into
    # a literal "<MagicMock ...>" directory at the repo root (issue #249).
    engine_settings.paths.data_dir = str(tmp_path)
    return engine_settings


def _adapter() -> Any:
    adapter = MagicMock()
    adapter.create_vision_job_with_pages = MagicMock(return_value="job_xyz")
    adapter.transition_source_status = MagicMock(return_value=True)
    adapter.list_vision_page_descriptions = MagicMock(return_value=[])
    adapter.increment_source_counter = MagicMock()
    return adapter


@pytest.mark.asyncio
async def test_full_mode_over_page_ceiling_fails_without_vision_job(
    monkeypatch, tmp_path: Path
) -> None:
    """10 image pages + ceiling=5 (full mode) -> raise, no vision job created."""
    monkeypatch.setattr(indexing_handler, "_get_active_vision_model", lambda s: "fake-vision")
    queue_client = MagicMock()
    queue_client.enqueue_task = AsyncMock()
    monkeypatch.setattr(indexing_handler, "queue_client", queue_client)

    adapter = _adapter()

    with pytest.raises(SourceFanoutLimitExceededError):
        await indexing_handler._apply_vision_processing(
            documents=_documents_with_pages(10),
            file_id="src-big",
            filepath="/tmp/huge.pdf",
            enable_vision=True,
            engine_settings=_engine_settings(tmp_path, vision_max_pages=5),
            database_name="default",
            data_dir="/tmp",
            adapter=adapter,
            analysis_depth="full",
        )

    # No vision job + page rows were created, and no per-page task enqueued.
    adapter.create_vision_job_with_pages.assert_not_called()
    queue_client.enqueue_task.assert_not_called()


@pytest.mark.asyncio
async def test_full_mode_at_page_ceiling_proceeds(monkeypatch, tmp_path: Path) -> None:
    """5 image pages + ceiling=5 (boundary) -> proceeds, vision job created."""
    monkeypatch.setattr(indexing_handler, "_get_active_vision_model", lambda s: "fake-vision")
    queue_client = MagicMock()
    queue_client.enqueue_task = AsyncMock()
    monkeypatch.setattr(indexing_handler, "queue_client", queue_client)

    adapter = _adapter()

    await indexing_handler._apply_vision_processing(
        documents=_documents_with_pages(5),
        file_id="src-ok",
        filepath="/tmp/ok.pdf",
        enable_vision=True,
        engine_settings=_engine_settings(tmp_path, vision_max_pages=5),
        database_name="default",
        data_dir="/tmp",
        adapter=adapter,
        analysis_depth="full",
    )

    adapter.create_vision_job_with_pages.assert_called_once()
    assert len(adapter.create_vision_job_with_pages.call_args.kwargs["pages"]) == 5


@pytest.mark.asyncio
async def test_quick_mode_ignores_page_ceiling(monkeypatch, tmp_path: Path) -> None:
    """Quick mode samples below the cap regardless of vision_max_pages — the
    ceiling must not trip on a Quick import of a huge PDF.
    """
    monkeypatch.setattr(indexing_handler, "_get_active_vision_model", lambda s: "fake-vision")
    queue_client = MagicMock()
    queue_client.enqueue_task = AsyncMock()
    monkeypatch.setattr(indexing_handler, "queue_client", queue_client)

    adapter = _adapter()

    # 400 pages, full-mode ceiling tiny (5), but Quick samples to 20.
    await indexing_handler._apply_vision_processing(
        documents=_documents_with_pages(400),
        file_id="src-quick",
        filepath="/tmp/book.pdf",
        enable_vision=True,
        engine_settings=_engine_settings(
            tmp_path, vision_max_pages=5, vision_quick_sample_max_pages=20
        ),
        database_name="default",
        data_dir="/tmp",
        adapter=adapter,
        analysis_depth="quick",
    )

    adapter.create_vision_job_with_pages.assert_called_once()
    assert len(adapter.create_vision_job_with_pages.call_args.kwargs["pages"]) == 20
