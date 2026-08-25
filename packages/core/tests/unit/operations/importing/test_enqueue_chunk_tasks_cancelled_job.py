# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``_enqueue_chunk_tasks`` honours a refused extraction-job start.

A re-extract cancels the source's active job (see
``SourceIndexingMixin.reset_to_indexed_for_re_extract``). If that cancel
lands while the analysis handler is between "job resolved" and "chunks
dispatched", the dispatch must stop: enqueueing chunk tasks for an
abandoned job burns queue slots ahead of the fresh job and overwrites the
just-reset source's progress text with "Analyzing chunk 1/N".
``start_extraction_job`` refuses to restart a terminal job and reports it,
and this is the second of its two production callers (the finalizer being
the first) that has to act on the refusal.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.operations.importing.import_service import (
    ImportOperationsService,
)


def _make_service(adapter: MagicMock) -> ImportOperationsService:
    """Build an ImportOperationsService whose storage port is ``adapter``."""
    from chaoscypher_core.settings import EngineSettings

    return ImportOperationsService(
        graph_repository=MagicMock(),
        config_manager=MagicMock(),
        source_manager=MagicMock(),
        trigger_service=MagicMock(),
        llm_service=AsyncMock(),
        source_repository=adapter,
        chunking_service=MagicMock(),
        indexing_service=MagicMock(),
        engine_settings=EngineSettings(current_database="default"),
    )


def _make_adapter(*, start_ok: bool) -> MagicMock:
    """Adapter mock whose ``start_extraction_job`` reports ``start_ok``."""
    adapter = MagicMock()
    adapter.list_extraction_tasks_for_job.return_value = []
    adapter.create_chunk_tasks_batch.return_value = []
    adapter.orphan_chunk_tasks_outside_range.return_value = 0
    adapter.list_extraction_tasks_by_status.return_value = []
    adapter.start_extraction_job.return_value = start_ok
    return adapter


def _make_settings() -> MagicMock:
    """Minimal settings namespace read by the dispatch path."""
    settings = MagicMock()
    settings.current_database = "default"
    return settings


def _progress_texts(adapter: MagicMock) -> list[str]:
    """Every step-description string written via ``update_step_progress``."""
    texts: list[str] = []
    for call in adapter.update_step_progress.call_args_list:
        if len(call.args) > 3:
            texts.append(str(call.args[3]))
        elif "step_description" in call.kwargs:
            texts.append(str(call.kwargs["step_description"]))
    return texts


@pytest.mark.asyncio
async def test_enqueue_bails_out_when_the_job_was_cancelled() -> None:
    """A refused start stops the dispatch before any queue or progress write."""
    adapter = _make_adapter(start_ok=False)
    service = _make_service(adapter)

    dispatched = await service._enqueue_chunk_tasks(
        adapter=adapter,
        job_id="job-1",
        file_id="src-1",
        hierarchical_groups=[{"id": "g0", "small_chunk_ids": ["c0"]}],
        settings=_make_settings(),
    )

    assert dispatched == 0
    adapter.start_extraction_job.assert_called_once_with("job-1")
    # No chunk tasks looked up, so none enqueued.
    adapter.list_extraction_tasks_by_status.assert_not_called()
    # And the reset source's progress text is left alone.
    assert not any("Analyzing chunk" in text for text in _progress_texts(adapter))


@pytest.mark.asyncio
async def test_enqueue_proceeds_when_the_job_starts_normally() -> None:
    """The bail-out is scoped to refusals — a live job still dispatches."""
    adapter = _make_adapter(start_ok=True)
    service = _make_service(adapter)

    dispatched = await service._enqueue_chunk_tasks(
        adapter=adapter,
        job_id="job-1",
        file_id="src-1",
        hierarchical_groups=[{"id": "g0", "small_chunk_ids": ["c0"]}],
        settings=_make_settings(),
    )

    # Zero here means "every task row is already terminal", reached via the
    # normal path — the lookup ran and the initial progress was written.
    assert dispatched == 0
    adapter.list_extraction_tasks_by_status.assert_called_once()
    assert any("Analyzing chunk 1/1" in text for text in _progress_texts(adapter))
