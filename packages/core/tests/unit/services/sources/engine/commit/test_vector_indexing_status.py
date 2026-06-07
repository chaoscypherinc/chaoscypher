# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workstream 10 - Vector search visibility status transitions.

The commit pipeline owns three transitions on ``SourceRow``:

* ``pending`` is the post-upload default; commit confirms it at the
  start of the post-transaction indexing phase.
* ``indexed`` (with ``vector_indexed_at`` set) when both node and
  chunk vector writes succeed.
* ``degraded`` when an indexing call raises and ``_enqueue_search_retry``
  defers the work to the orphan-sweep worker.

Retry exhaustion (sweep worker reaches ``max_attempts``) flips the
status from ``degraded`` to ``failed``; that path is exercised in
``packages/neuron/tests/unit/test_search_sweep.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceStatus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _build_commit_service(adapter: SqliteAdapter) -> object:
    """Construct a SourceCommitService against the test adapter."""
    from chaoscypher_core.adapters.sqlite.engine import get_engine
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService
    from chaoscypher_core.settings import EngineSettings

    graph_repository = GraphRepository(
        session=adapter.session,
        database_name=adapter.database_name,
    )
    settings = EngineSettings()
    search_repository = SearchRepository(
        engine=get_engine(adapter.db_path),
        vector_dim=4,
        embedding_model="test-model",
    )
    return SourceCommitService(
        graph_repository=graph_repository,
        source_repository=adapter,
        sources_repository=adapter,
        indexing_repository=adapter,
        search_repository=search_repository,
        settings=settings,
    )


def _seed_extracted_source(adapter: SqliteAdapter, source_id: str) -> None:
    """Seed an EXTRACTED source row with no entities (commit_empty path)."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": "vec_status.md",
            "filepath": "/tmp/vec_status.md",
            "file_type": "markdown",
            "file_size": 99,
            "content_hash": f"hash-{source_id}",
            "status": SourceStatus.EXTRACTED.value,
        }
    )
    adapter.complete_extraction(
        source_id=source_id,
        entities=[],
        relationships=[],
        forced_domain=None,
        detected_domain=None,
    )


_EMPTY_COMMIT_DATA: dict[str, object] = {
    "entities": [],
    "relationships": [],
    "create_templates": False,
    "suggested_templates": [],
    "suggested_edge_templates": [],
}


@pytest.mark.asyncio
async def test_commit_marks_indexed_on_success(
    adapter_with_default_templates: SqliteAdapter,
) -> None:
    """A clean commit ends with status='indexed' and vector_indexed_at set."""
    adapter = adapter_with_default_templates
    source_id = "src_vec_indexed"

    _seed_extracted_source(adapter, source_id)
    service = _build_commit_service(adapter)

    await service.commit(
        file_id=source_id,
        commit_data=_EMPTY_COMMIT_DATA,
        file_info={"id": source_id, "database_name": adapter.database_name},
    )

    assert adapter.session is not None
    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, source_id)
    assert row is not None
    assert row.vector_indexing_status == "indexed", (
        f"expected 'indexed' after successful commit, got {row.vector_indexing_status!r}"
    )
    assert row.vector_indexed_at is not None, (
        "vector_indexed_at must be timestamped on indexing success"
    )


@pytest.mark.asyncio
async def test_commit_marks_degraded_on_indexing_failure(
    adapter_with_default_templates: SqliteAdapter,
) -> None:
    """A vector-indexing failure leaves status='degraded' (retry queued)."""
    adapter = adapter_with_default_templates
    source_id = "src_vec_degraded"

    _seed_extracted_source(adapter, source_id)
    service = _build_commit_service(adapter)

    # Force the post-transaction chunk indexing to blow up so the commit
    # path lands in the _enqueue_search_retry branch.
    with patch.object(
        service,
        "_index_chunks_to_vector_search",
        side_effect=RuntimeError("vec exploded"),
    ):
        await service.commit(
            file_id=source_id,
            commit_data=_EMPTY_COMMIT_DATA,
            file_info={"id": source_id, "database_name": adapter.database_name},
        )

    assert adapter.session is not None
    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, source_id)
    assert row is not None
    assert row.vector_indexing_status == "degraded", (
        f"expected 'degraded' after retry-queued failure, got {row.vector_indexing_status!r}"
    )
    assert row.vector_indexed_at is None, (
        "vector_indexed_at must remain unset until a successful index lands"
    )


@pytest.mark.asyncio
async def test_retry_exhausted_marks_failed(
    adapter_with_default_templates: SqliteAdapter,
) -> None:
    """The ``mark_search_indexing_failed`` helper flips status to 'failed'."""
    from chaoscypher_core.services.quality.counters import (
        mark_search_indexing_failed,
    )

    adapter = adapter_with_default_templates
    source_id = "src_vec_failed"

    _seed_extracted_source(adapter, source_id)
    service = _build_commit_service(adapter)

    # First land in degraded via a real indexing failure on commit.
    with patch.object(
        service,
        "_index_chunks_to_vector_search",
        side_effect=RuntimeError("vec exploded"),
    ):
        await service.commit(
            file_id=source_id,
            commit_data=_EMPTY_COMMIT_DATA,
            file_info={"id": source_id, "database_name": adapter.database_name},
        )

    assert adapter.session is not None
    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, source_id)
    assert row is not None
    assert row.vector_indexing_status == "degraded"

    # Now exhaust retries — this is what the search-sweep worker does
    # when an entry crosses ``max_attempts``.
    mark_search_indexing_failed(
        adapter=adapter,
        source_id=source_id,
        database_name=adapter.database_name,
    )

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, source_id)
    assert row is not None
    assert row.vector_indexing_status == "failed", (
        f"expected 'failed' after retry exhaustion, got {row.vector_indexing_status!r}"
    )
