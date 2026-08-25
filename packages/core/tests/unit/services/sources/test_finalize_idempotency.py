# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for finalize_extraction_handler status short-circuit.

The finalize handler can be re-dispatched by the queue reconciler
or the source reconciler after a crash. If the
source has already moved past the ``extracting`` phase, the handler
must return immediately without re-running aggregation, dedup, or
storage writes.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["extracted", "committing", "committed"])
async def test_finalize_skips_terminal_states(terminal_status: str) -> None:
    """If source.status is already terminal relative to extraction, skip finalization.

    The finalizer returns a skip result without touching aggregate/dedupe.
    """
    from chaoscypher_core.operations.extraction.extraction_finalizer import (
        finalize_extraction_handler,
    )

    source_repo = MagicMock()
    source_repo.get_source = MagicMock(
        return_value={
            "id": "src-1",
            "status": terminal_status,
            "database_name": "default",
        }
    )

    result = await finalize_extraction_handler(
        graph_repository=MagicMock(),
        llm_service=AsyncMock(),
        source_repository=source_repo,
        chunk_extraction_service=MagicMock(),
        data={
            "source_id": "src-1",
            "job_id": "job-1",
            "database_name": "default",
        },
    )

    assert result == {"skipped": "already_finalized", "status": terminal_status}
    source_repo.get_source.assert_called_once_with("src-1", "default")
    # Aggregation path must not be touched
    source_repo.get_completed_chunk_results.assert_not_called()


@pytest.mark.asyncio
async def test_finalize_skips_when_source_missing() -> None:
    """A source row that was deleted mid-flight is a skip, not an error."""
    from chaoscypher_core.operations.extraction.extraction_finalizer import (
        finalize_extraction_handler,
    )

    source_repo = MagicMock()
    source_repo.get_source = MagicMock(return_value=None)

    result = await finalize_extraction_handler(
        graph_repository=MagicMock(),
        llm_service=AsyncMock(),
        source_repository=source_repo,
        chunk_extraction_service=MagicMock(),
        data={
            "source_id": "src-1",
            "job_id": "job-1",
            "database_name": "default",
        },
    )

    assert result == {"skipped": "source_missing"}
    source_repo.get_completed_chunk_results.assert_not_called()


@pytest.mark.asyncio
async def test_finalize_does_not_resurrect_a_cancelled_job() -> None:
    """A finalize queued before a re-extract must not revive the cancelled job.

    Sequence: the last chunk completes and enqueues finalize, then the user
    re-extracts (which cancels the job and resets the source to INDEXED),
    then this finalize runs. ``start_extraction_job`` refuses the terminal
    job, so the handler skips instead of flipping it back to ``running`` —
    otherwise the fresh analysis task would find it "active" and resume the
    run the user just replaced, and the old chunks would be aggregated onto
    the reset source.
    """
    from chaoscypher_core.operations.extraction.extraction_finalizer import (
        finalize_extraction_handler,
    )

    source_repo = MagicMock()
    source_repo.get_source = MagicMock(
        return_value={
            "id": "src-1",
            "status": "indexed",  # already reset by the re-extract
            "database_name": "default",
            "is_paused": False,
        }
    )
    source_repo.get_system_state = MagicMock(return_value=None)
    # The adapter refuses to restart a cancelled job.
    source_repo.start_extraction_job = MagicMock(return_value=False)

    result = await finalize_extraction_handler(
        graph_repository=MagicMock(),
        llm_service=AsyncMock(),
        source_repository=source_repo,
        chunk_extraction_service=MagicMock(),
        data={
            "source_id": "src-1",
            "job_id": "job-1",
            "database_name": "default",
        },
    )

    assert result == {"skipped": "job_gone", "job_id": "job-1"}
    source_repo.start_extraction_job.assert_called_once_with("job-1")
    # No aggregation, no terminal writes on the reset source.
    source_repo.get_completed_chunk_results.assert_not_called()
    source_repo.get_chunk_tasks_by_job.assert_not_called()
    source_repo.complete_extraction.assert_not_called()
    source_repo.complete_extraction_job.assert_not_called()
