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
