# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: an aborted-then-retried source resumes at the correct stage.

Audit fix #C2 — before Task 3's abort_processing translation, the abort
path wrote the SourceStatus gerund (e.g. "extracting") into error_stage, but
the retry endpoint only matched the noun form ("extraction"). The mismatch
caused every aborted source to restart from PENDING regardless of where it
was in the pipeline.

Task 3 installed ``_ABORT_STAGE_MAP`` to translate gerunds → nouns at the
abort write-site.  Task 4 makes the retry decode type-safe by comparing
against ``SourceErrorStage`` enum members rather than bare strings.

These three parametrised cases lock the full contract going forward:
- aborted from EXTRACTING  → resumes at INDEXED  (re-extract only)
- aborted from COMMITTING  → resumes at EXTRACTED (re-commit only)
- aborted from INDEXING    → resumes at PENDING   (full restart)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.models import SourceStatus
from chaoscypher_cortex.features.sources.service import (
    _ABORT_STAGE_MAP,
    SourceService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings() -> MagicMock:
    """Return minimal settings stub for SourceService."""
    settings = MagicMock()
    settings.pagination.default_page_size = 50
    settings.pagination.max_page_size = 1000
    settings.pagination.extraction_tasks_page_size = 25
    settings.batching.template_name_cache_size = 100
    settings.priorities.background = 50
    settings.data_dir = "/tmp/cc-data"
    return settings


def _make_service(
    *,
    adapter: MagicMock | None = None,
    engine_service: MagicMock | None = None,
    database_name: str = "default",
) -> SourceService:
    """Return a SourceService with mock collaborators."""
    if adapter is None:
        adapter = MagicMock()
    # Ensure system-pause guard passes unless the test explicitly overrides.
    adapter.get_system_state.return_value = {"processing_paused": False}
    return SourceService(
        engine_service=engine_service or MagicMock(),
        database_name=database_name,
        settings=_settings(),
        storage_adapter=adapter,
    )


def _aborted_source(
    source_id: str,
    error_stage: str,
) -> dict[str, Any]:
    """Return a source dict in ERROR state with the given error_stage.

    Includes all file-metadata fields that ``_dispatch_retry_task`` reads
    so the retry path does not fail due to missing keys.
    """
    return {
        "id": source_id,
        "database_name": "default",
        "status": SourceStatus.ERROR,
        "error_stage": error_stage,
        "error_message": "Aborted by user",
        "filepath": "/data/test.txt",
        "file_type": "text",
        "filename": "test.txt",
        "extraction_depth": "full",
        "forced_domain": None,
        "extraction_domain_auto": True,
        "is_paused": False,
        "recovery_attempts": 0,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Regression test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("aborted_from", "expected_resume_status"),
    [
        (SourceStatus.EXTRACTING, SourceStatus.INDEXED),
        (SourceStatus.COMMITTING, SourceStatus.EXTRACTED),
        (SourceStatus.INDEXING, SourceStatus.PENDING),
    ],
    ids=["extracting→indexed", "committing→extracted", "indexing→pending"],
)
@pytest.mark.asyncio
async def test_abort_then_retry_resumes_at_stage(
    aborted_from: str,
    expected_resume_status: str,
) -> None:
    """Abort then retry: source resumes at the pre-failure stage (audit fix #C2).

    The abort path translates the in-flight SourceStatus (gerund) into the
    matching SourceErrorStage (noun) via ``_ABORT_STAGE_MAP``.  The retry
    endpoint reads that noun and routes to the correct resume status.

    This test drives the full round-trip at the service layer: mock the
    engine returning a post-abort ERROR source, call ``retry_source``, and
    assert that ``reset_for_retry`` received the expected ``new_status``.
    """
    # Step 1: compute the error_stage the abort path would have written.
    expected_error_stage = _ABORT_STAGE_MAP[aborted_from].value

    source_id = "src_regression_c2"

    # Step 2: build the post-abort source dict.
    source_after_abort = _aborted_source(source_id, expected_error_stage)

    # Step 3: wire mocks.
    adapter = MagicMock()
    # Return empty extraction_results for the EXTRACTED-resume branch's prefetch.
    adapter.get_extraction_results.return_value = {"extraction_results": {}}

    engine = MagicMock()
    # First call: initial fetch in retry_source; second call: post-reset read.
    engine.get_source.side_effect = [
        source_after_abort,
        {**source_after_abort, "status": expected_resume_status, "error_stage": None},
    ]

    service = _make_service(adapter=adapter, engine_service=engine)

    # Step 4: call retry_source with the queue dispatch patched out.
    with (
        patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
        patch("chaoscypher_cortex.features.sources.service.event_bus"),
    ):
        mock_queue.queue_import_indexing = AsyncMock(
            return_value={"task_id": "t1", "status": "queued"}
        )
        mock_queue.queue_import_analysis = AsyncMock(return_value="t2")
        mock_queue.queue_import_commit = AsyncMock(return_value="t3")

        await service.retry_source(source_id)

    # Step 5: assert reset_for_retry was called with the correct resume status.
    adapter.reset_for_retry.assert_called_once()
    call_kwargs = adapter.reset_for_retry.call_args.kwargs
    assert call_kwargs["new_status"] == expected_resume_status, (
        f"Audit fix #C2 regression: aborting from {aborted_from!r} should resume "
        f"at {expected_resume_status!r}, but reset_for_retry was called with "
        f"new_status={call_kwargs['new_status']!r}. "
        f"Check _ABORT_STAGE_MAP and the retry decode in SourceService.retry_source."
    )
