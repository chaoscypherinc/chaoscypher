# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""End-to-end integration test for indexing resume semantics.

This test exercises the full ``_embed_unembedded_chunks`` helper
against a real SqliteAdapter (file-backed, per-test tmp_path). It
seeds a source with half the chunks already ``embedded_at``-stamped
and half unembedded, then runs the helper and verifies that only
the unembedded half gets processed — which is exactly the resume
guarantee for crash-recovery.

Runs as a regular pytest case (no external services needed). Lives
under tests/integration because it goes through the real adapter
rather than mocking it.
"""

from datetime import UTC, datetime

import pytest

from chaoscypher_core.operations.importing.embedding_handler import (
    _embed_unembedded_chunks,
)


@pytest.mark.asyncio
async def test_indexing_resume_after_partial_embed(integration_adapter) -> None:
    """Half-embedded source: _embed_unembedded_chunks only touches the unembedded half.

    The adapter's unembedded list is empty after.
    """
    source_id = "src-resume-1"

    # Seed the source row first — document_chunks.source_id has a FK
    integration_adapter.create_source(
        {
            "id": source_id,
            "database_name": integration_adapter.database_name,
            "filename": "big.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 10240,
            "content_hash": f"hash-{source_id}",
            "status": "indexing",
        }
    )

    # Seed 10 chunks: indices 0-4 already embedded, 5-9 not yet
    now = datetime.now(UTC)
    for i in range(10):
        integration_adapter.create_chunk(
            {
                "id": f"chunk-{i}",
                "database_name": integration_adapter.database_name,
                "source_id": source_id,
                "chunk_index": i,
                "content": f"chunk {i} content",
                "status": "indexed",
                "embedded_at": now if i < 5 else None,
            }
        )

    # Sanity check: adapter sees 5 unembedded chunks before the run
    pre_unembedded = integration_adapter.list_unembedded_chunks(
        source_id=source_id,
        database_name=integration_adapter.database_name,
    )
    assert len(pre_unembedded) == 5
    assert {c["chunk_index"] for c in pre_unembedded} == {5, 6, 7, 8, 9}

    # Fake indexing service — records which chunks it was asked to
    # embed and returns their count (matching the real embed_chunks
    # contract from Task 5).
    # Phase 7 audit-remediation (2026-05-09): settings.search.vector_dimensions
    # added to support P1 #7 dim-at-chunk-write recording.
    class _FakeSettings:
        class _Search:
            vector_dimensions = 384

        search = _Search()

    class _FakeIndexingService:
        settings = _FakeSettings()

        def __init__(self) -> None:
            self.embedded_chunks: list[dict] = []

        async def embed_chunks(
            self,
            *,
            chunks: list[dict],
            source_id: str,
            database_name: str,
            progress_callback=None,
            cancellation_check=None,
            expected_dimensions: int | None = None,
        ) -> int:
            self.embedded_chunks.extend(chunks)
            return len(chunks)

    indexing_service = _FakeIndexingService()

    count = await _embed_unembedded_chunks(
        source_id=source_id,
        database_name=integration_adapter.database_name,
        adapter=integration_adapter,
        indexing_service=indexing_service,
    )

    # Only the unembedded half went through the embedder
    assert count == 5
    assert {c["chunk_index"] for c in indexing_service.embedded_chunks} == {5, 6, 7, 8, 9}

    # And the adapter now considers everything embedded
    post_unembedded = integration_adapter.list_unembedded_chunks(
        source_id=source_id,
        database_name=integration_adapter.database_name,
    )
    assert post_unembedded == []


@pytest.mark.asyncio
async def test_indexing_resume_is_noop_when_all_chunks_already_embedded(
    integration_adapter,
) -> None:
    """Re-running _embed_unembedded_chunks on a fully-embedded source is a no-op.

    No embedder call, zero count.
    """
    source_id = "src-resume-2"

    integration_adapter.create_source(
        {
            "id": source_id,
            "database_name": integration_adapter.database_name,
            "filename": "done.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 1024,
            "content_hash": f"hash-{source_id}",
            "status": "indexing",
        }
    )

    now = datetime.now(UTC)
    for i in range(3):
        integration_adapter.create_chunk(
            {
                "id": f"chunk-done-{i}",
                "database_name": integration_adapter.database_name,
                "source_id": source_id,
                "chunk_index": i,
                "content": f"chunk {i}",
                "status": "indexed",
                "embedded_at": now,
            }
        )

    class _UnusedIndexingService:
        def __init__(self) -> None:
            self.called = False

        async def embed_chunks(self, **kwargs) -> int:
            self.called = True
            return 0

    indexing_service = _UnusedIndexingService()

    count = await _embed_unembedded_chunks(
        source_id=source_id,
        database_name=integration_adapter.database_name,
        adapter=integration_adapter,
        indexing_service=indexing_service,
    )

    assert count == 0
    assert indexing_service.called is False
