# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests that source handlers return {skipped: paused} when paused.

Every source-processing handler must call check_paused at the top and
return cleanly without consuming retry budget or touching real work.
These tests construct minimal mocks and verify that the handler's
return value is exactly {"skipped": "paused"} when the adapter
reports the source as paused.

Rather than fully mocking each handler's downstream dependencies, we
rely on the pause check being the FIRST thing the handler does — if
it runs before any other work, none of those downstream mocks are
touched and the test is minimal.
"""

from unittest.mock import MagicMock

import pytest


def _paused_source_adapter() -> MagicMock:
    """Return a MagicMock adapter reporting the source as paused."""
    adapter = MagicMock()
    adapter.get_source = MagicMock(
        return_value={
            "id": "s-1",
            "is_paused": True,
            "paused_reason": "test",
            "status": "indexing",
            "database_name": "default",
        }
    )
    adapter.get_system_state = MagicMock(return_value={"processing_paused": False})
    return adapter


def _system_paused_adapter() -> MagicMock:
    """Return a MagicMock adapter reporting system-wide pause."""
    adapter = MagicMock()
    adapter.get_source = MagicMock(
        return_value={
            "id": "s-1",
            "is_paused": False,
            "status": "indexing",
            "database_name": "default",
        }
    )
    adapter.get_system_state = MagicMock(
        return_value={"processing_paused": True, "processing_paused_reason": "deploy"}
    )
    return adapter


# ---------------------------------------------------------------------------
# 1. handle_index_document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_document_handler_skips_when_source_paused() -> None:
    from chaoscypher_core.operations.importing.indexing_handler import (
        handle_index_document,
    )

    adapter = _paused_source_adapter()

    result = await handle_index_document(
        data={
            "file_id": "s-1",
            "file_info": {"filepath": "/tmp/test.pdf"},
        },
        source_repository=adapter,
        chunking_service=MagicMock(),
    )
    assert result == {"skipped": "paused"}
    # If the pause check fires first, we never touch the loader or chunker
    adapter.start_indexing.assert_not_called()


@pytest.mark.asyncio
async def test_index_document_handler_skips_when_system_paused() -> None:
    from chaoscypher_core.operations.importing.indexing_handler import (
        handle_index_document,
    )

    adapter = _system_paused_adapter()

    result = await handle_index_document(
        data={
            "file_id": "s-1",
            "file_info": {"filepath": "/tmp/test.pdf"},
        },
        source_repository=adapter,
        chunking_service=MagicMock(),
    )
    assert result == {"skipped": "paused"}
    adapter.start_indexing.assert_not_called()


# ---------------------------------------------------------------------------
# 2. ImportOperationsService._import_analysis_handler
# ---------------------------------------------------------------------------


def _build_import_service(adapter: MagicMock):
    from chaoscypher_core.operations.importing.import_service import (
        ImportOperationsService,
    )

    return ImportOperationsService(
        graph_repository=MagicMock(),
        config_manager=MagicMock(),
        source_manager=MagicMock(),
        trigger_service=MagicMock(),
        llm_service=MagicMock(),
        source_repository=adapter,
        chunking_service=MagicMock(),
        indexing_service=MagicMock(),
    )


@pytest.mark.asyncio
async def test_analysis_handler_skips_when_source_paused() -> None:
    adapter = _paused_source_adapter()
    service = _build_import_service(adapter)

    result = await service._import_analysis_handler(data={"file_id": "s-1", "file_info": {}})
    assert result == {"skipped": "paused"}
    adapter.try_claim_extraction.assert_not_called()


@pytest.mark.asyncio
async def test_analysis_handler_skips_when_system_paused() -> None:
    adapter = _system_paused_adapter()
    service = _build_import_service(adapter)

    result = await service._import_analysis_handler(data={"file_id": "s-1", "file_info": {}})
    assert result == {"skipped": "paused"}
    adapter.try_claim_extraction.assert_not_called()


# ---------------------------------------------------------------------------
# 3. ImportOperationsService._import_commit_handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_handler_skips_when_source_paused() -> None:
    adapter = _paused_source_adapter()
    service = _build_import_service(adapter)

    result = await service._import_commit_handler(
        data={
            "file_id": "s-1",
            "commit_data": {"entities": [], "relationships": []},
            "file_info": {},
        }
    )
    assert result == {"skipped": "paused"}


# ---------------------------------------------------------------------------
# 4. ChunkExtractionOperationsService._extract_chunk_handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_chunk_handler_skips_when_source_paused() -> None:
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    adapter = MagicMock()
    adapter.get_source = MagicMock(
        return_value={"id": "s-1", "is_paused": True, "paused_reason": "test"}
    )
    adapter.get_system_state = MagicMock(return_value={"processing_paused": False})
    # The handler derives source_id from the job record — return a job that
    # points at s-1 so pause check has a source_id to look up.
    adapter.get_extraction_job = MagicMock(
        return_value={
            "id": "job-1",
            "source_id": "s-1",
            "status": "running",
            "extraction_config": None,
        }
    )
    # Stale-task detection short-circuits return None (non-stale) so the
    # handler proceeds to the pause check.
    adapter.get_chunk_task = MagicMock(return_value={"id": "ct-1", "status": "pending"})
    # Rehydration: handler now fetches chunk text from the DB by IDs.
    # Returning matching content lets the handler reach the pause guard.
    adapter.get_chunks_by_ids = MagicMock(return_value=[{"id": "sc-1", "content": "hello"}])

    service = ChunkExtractionOperationsService(
        graph_repository=MagicMock(),
        config_manager=MagicMock(),
        llm_service=MagicMock(),
        source_repository=adapter,
    )

    result = await service._extract_chunk_handler(
        data={
            "chunk_task_id": "ct-1",
            "job_id": "job-1",
            "database_name": "default",
            "small_chunk_ids": ["sc-1"],
            "chunk_index": 0,
        }
    )
    assert result == {"skipped": "paused"}
    # The pause check must happen before the LLM call — start_chunk_task
    # is a good signal that real work started.
    adapter.start_chunk_task_with_input.assert_not_called()


# ---------------------------------------------------------------------------
# 5. finalize_extraction_handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_handler_skips_when_source_paused() -> None:
    from chaoscypher_core.operations.extraction.extraction_finalizer import (
        finalize_extraction_handler,
    )

    adapter = _paused_source_adapter()
    # finalize's idempotency guard runs get_source to detect an already
    # finalized source. That call returns our paused source — status
    # 'indexing' is not in the already-finalized set, so the pause
    # check should fire next.

    result = await finalize_extraction_handler(
        graph_repository=MagicMock(),
        llm_service=MagicMock(),
        source_repository=adapter,
        chunk_extraction_service=MagicMock(),
        data={
            "job_id": "job-1",
            "source_id": "s-1",
            "database_name": "default",
        },
    )
    assert result == {"skipped": "paused"}
    adapter.start_extraction_job.assert_not_called()
