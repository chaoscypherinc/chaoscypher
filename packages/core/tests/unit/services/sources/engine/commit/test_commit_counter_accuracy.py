# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""commit_edges_created should match actual rows in graph_edges for the source.

Regression test for the counter drift bug: upsert_edges_batch returns both
newly-inserted and pre-existing rows. The old code passed len(returned_list)
to complete_commit, which overcounted by the number of dedup hits.

This test exercises:
- A relationship duplicated across two entries (dedup hit on the edge upsert)
- An inverse-mapped relationship (exercises the +inverse path in prepare_relationship_edges)

Expected: source.commit_edges_created == SELECT COUNT(*) FROM graph_edges WHERE source_id = ?
"""

from __future__ import annotations

import pytest
from sqlmodel import func, select

from chaoscypher_core.adapters.sqlite.models import GraphEdge, SourceRow
from chaoscypher_core.models import SourceStatus


def _build_commit_service(adapter):
    """Construct SourceCommitService wired from the test adapter."""
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


def _seed_extracted_source(adapter, source_id: str) -> None:
    """Seed a source row in EXTRACTED state (status only, no extraction_results needed)."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": "counter_test.md",
            "filepath": "/tmp/counter_test.md",
            "file_type": "markdown",
            "file_size": 99,
            "content_hash": f"hash-{source_id}",
            "status": SourceStatus.EXTRACTED.value,
        }
    )
    # complete_extraction sets commit_complete=False and status=EXTRACTED
    adapter.complete_extraction(
        source_id=source_id,
        entities=[],
        relationships=[],
        forced_domain=None,
        detected_domain=None,
    )


# Commit payload with a duplicate edge and an inverse-mapped relationship.
# Three relationships in the input, but only three unique edges should be inserted:
#   1. serves(Alpha->Beta)
#   2. is_served_by(Beta->Alpha)   [inverse of serves]
#   3. interacts_with(Beta->Gamma)
#
# The second "serves" entry is identical to the first and must be deduped by
# upsert_edges_batch.  With the bug, the counter reports 4 (input list size
# including inverse) for the "serves" pair processed twice, but the DB has 3 rows.
#
# Note: prepare_relationship_edges appends an inverse edge for each forward edge
# that has an inverse mapping.  So from 3 relationships with inverse_relationships
# = {"serves": "is_served_by"}, the edges_to_create list is:
#   serves(A->B), is_served_by(B->A), serves(A->B), is_served_by(B->A), interacts_with(B->C)
# 5 items → 3 unique inserts.  Counter bug: 5 != 3.
_COMMIT_DATA_WITH_DEDUP = {
    "entities": [
        {"name": "Alpha", "type": "Person", "properties": {}},  # index 0
        {"name": "Beta", "type": "Person", "properties": {}},  # index 1
        {"name": "Gamma", "type": "Person", "properties": {}},  # index 2
    ],
    "relationships": [
        # Duplicate: same source+target+type emitted twice (triggers dedup hit)
        {"source": 0, "target": 1, "type": "serves"},
        {"source": 0, "target": 1, "type": "serves"},
        # Distinct edge
        {"source": 1, "target": 2, "type": "interacts_with"},
    ],
    # inverse_relationships exercises the +inverse path in prepare_relationship_edges
    "inverse_relationships": {"serves": "is_served_by"},
    "create_templates": False,
    "suggested_templates": [],
    "suggested_edge_templates": [],
}


@pytest.mark.asyncio
async def test_commit_edges_created_matches_row_count(
    adapter_with_default_templates,
) -> None:
    """commit_edges_created must equal SELECT COUNT(*) FROM graph_edges WHERE source_id=?

    With duplicate relationships in the extraction payload, upsert_edges_batch
    deduplicates and inserts fewer rows than the input list length.
    The counter must reflect inserted rows, not input list size.
    """
    adapter = adapter_with_default_templates
    source_id = "src_counter_test"

    _seed_extracted_source(adapter, source_id)
    service = _build_commit_service(adapter)

    await service.commit(
        file_id=source_id,
        commit_data=_COMMIT_DATA_WITH_DEDUP,
        file_info={"id": source_id, "database_name": adapter.database_name},
    )

    assert adapter.session is not None
    source_row = adapter.session.get(SourceRow, source_id)
    assert source_row is not None, "SourceRow must exist after commit"
    counter_value = source_row.commit_edges_created

    actual_row_count = adapter.session.exec(
        select(func.count(GraphEdge.id)).where(GraphEdge.source_id == source_id)
    ).one()

    assert actual_row_count == 3, (
        f"expected 3 unique rows (serves(A,B), is_served_by(B,A), interacts_with(B,C)) "
        f"but graph has {actual_row_count}"
    )
    assert counter_value == actual_row_count, (
        f"commit_edges_created counter ({counter_value}) does not match "
        f"actual graph_edges row count ({actual_row_count}) for source {source_id!r}. "
        f"The counter overstates by {counter_value - actual_row_count} "
        f"(likely dedup hits counted as new insertions)."
    )
