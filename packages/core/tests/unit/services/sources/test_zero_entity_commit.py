# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for zero-entity extraction commit path (BE-3).

Two test scenarios:
  3a -- Legitimate empty extraction commits as empty graph (chunks still promoted).
  3b -- Stale re-dispatch on an already-committed source is skipped (Cluster A safety).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter

from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.services.sources.engine.commit.service import (
    SourceCommitService,
)


# ------------------------------------------------------------------ #
#  Shared helpers (mirrors test_commit_atomicity.py)
# ------------------------------------------------------------------ #


def _build_commit_service(
    in_memory_adapter: SqliteAdapter,
) -> SourceCommitService:
    """Construct SourceCommitService wired from the test adapter.

    Mirrors the production wiring in import_service.py.  The adapter
    implements all three storage protocols via its mixins, so it is
    passed for source_repository, sources_repository, and
    indexing_repository simultaneously.
    """
    from chaoscypher_core.adapters.sqlite.engine import get_engine
    from chaoscypher_core.adapters.sqlite.repos import SearchRepository
    from chaoscypher_core.settings import EngineSettings

    graph_repository = GraphRepository(
        session=in_memory_adapter.session,
        database_name=in_memory_adapter.database_name,
    )
    settings = EngineSettings()
    search_repository = SearchRepository(
        engine=get_engine(in_memory_adapter.db_path),
        vector_dim=4,
        embedding_model="test-model",
    )
    return SourceCommitService(
        graph_repository=graph_repository,
        source_repository=in_memory_adapter,
        sources_repository=in_memory_adapter,
        indexing_repository=in_memory_adapter,
        search_repository=search_repository,
        settings=settings,
    )


def _seed_extracted_source(
    adapter: SqliteAdapter,
    source_id: str = "src_empty",
    *,
    commit_complete: bool = False,
    status: str | None = None,
) -> str:
    """Seed a source row in the EXTRACTED state (zero entities).

    Args:
        adapter: Adapter under test.
        source_id: ID to use for the new source row.
        commit_complete: Whether to mark the source as already committed.
        status: Override the source status (defaults to EXTRACTED).

    Returns:
        The source_id that was seeded.
    """
    effective_status = status or (
        SourceStatus.COMMITTED.value if commit_complete else SourceStatus.EXTRACTED.value
    )
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": "empty.md",
            "filepath": "/tmp/empty.md",
            "file_type": "markdown",
            "file_size": 42,
            "content_hash": "hash-empty",
            "status": effective_status,
        }
    )
    # complete_extraction transitions to status=extracted and stores empty results
    if not commit_complete:
        adapter.complete_extraction(
            source_id=source_id,
            entities=[],
            relationships=[],
            forced_domain=None,
            detected_domain=None,
        )
    else:
        # For the already-committed case: run extraction completion first so
        # the row exists in a valid extracted state, then simulate a completed
        # commit by calling complete_commit directly.
        adapter.complete_extraction(
            source_id=source_id,
            entities=[],
            relationships=[],
            forced_domain=None,
            detected_domain=None,
        )
        adapter.start_commit(source_id)
        adapter.complete_commit(
            source_id=source_id,
            nodes_created=0,
            edges_created=0,
            templates_created=0,
        )
    return source_id


# ------------------------------------------------------------------ #
#  Test 3a -- Legitimate empty extraction commits as empty graph
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_zero_entity_extraction_commits_empty(
    in_memory_adapter: SqliteAdapter,
) -> None:
    """Source with 0 extracted entities transitions to committed with 0 nodes/edges.

    Chunks remain promoted so they are visible for RAG/search.
    """
    from sqlmodel import select

    from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode, SourceRow

    source_id = _seed_extracted_source(in_memory_adapter)
    service = _build_commit_service(in_memory_adapter)

    result = await service.commit(
        file_id=source_id,
        commit_data={"entities": [], "relationships": []},
        file_info={"id": source_id, "database_name": "default"},
    )

    # Result must signal empty extraction (not a skip)
    assert result.get("empty_extraction") is True, (
        f"Expected empty_extraction=True in result, got: {result}"
    )
    assert result["created_nodes"] == []
    assert result["created_edges"] == []
    assert result["created_templates"] == []
    assert "skipped" not in result, f"Expected no skipped key, got: {result}"

    # Source must be committed
    in_memory_adapter.session.expire_all()
    row = in_memory_adapter.session.get(SourceRow, source_id)
    assert row is not None
    assert row.status == SourceStatus.COMMITTED, f"Expected COMMITTED, got {row.status}"
    assert row.commit_complete is True

    # Zero graph data
    nodes = list(in_memory_adapter.session.exec(select(GraphNode)))
    edges = list(in_memory_adapter.session.exec(select(GraphEdge)))
    assert nodes == [], f"Expected no nodes, got {nodes}"
    assert edges == [], f"Expected no edges, got {edges}"


# ------------------------------------------------------------------ #
#  Test 3b -- Stale re-dispatch on already-committed source is skipped
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_commit_skips_if_already_committed(
    in_memory_adapter: SqliteAdapter,
) -> None:
    """Stale re-dispatch on committed source leaves graph data untouched.

    Cluster A safety property: commit_complete=True triggers the
    already_committed skip path; any pre-existing graph data stays intact.
    """
    from sqlmodel import select

    from chaoscypher_core.adapters.sqlite.models import GraphNode, GraphTemplate, SourceRow

    source_id = _seed_extracted_source(in_memory_adapter, commit_complete=True)

    # GraphNode.template_id now enforces FK → graph_templates.id; seed
    # the referenced template first so the insert satisfies the constraint.
    in_memory_adapter.session.add(
        GraphTemplate(
            id="tmpl_pre_1",
            database_name=in_memory_adapter.database_name,
            name="PreExistingTemplate",
            template_type="node",
        )
    )
    in_memory_adapter.session.flush()

    # Inject a pre-existing node linked to this source so we can verify
    # it survives the stale re-dispatch attempt.
    pre_existing_node = GraphNode(
        id="node_pre_1",
        database_name=in_memory_adapter.database_name,
        graph_name="knowledge",
        label="PreExisting",
        source_id=source_id,
        template_id="tmpl_pre_1",
        properties={},
    )
    in_memory_adapter.session.add(pre_existing_node)
    in_memory_adapter.session.commit()

    service = _build_commit_service(in_memory_adapter)

    result = await service.commit(
        file_id=source_id,
        commit_data={"entities": [], "relationships": []},
        file_info={"id": source_id, "database_name": "default"},
    )

    # Must return the already_committed skip signal
    assert result.get("skipped") == "already_committed", (
        f"Expected skipped=already_committed, got: {result}"
    )

    # Pre-existing node must survive
    in_memory_adapter.session.expire_all()
    nodes = list(in_memory_adapter.session.exec(select(GraphNode)))
    assert len(nodes) == 1, f"Expected 1 pre-existing node, got {len(nodes)}: {nodes}"
    assert nodes[0].id == "node_pre_1"

    # Source must still be committed (unchanged)
    row = in_memory_adapter.session.get(SourceRow, source_id)
    assert row is not None
    assert row.status == SourceStatus.COMMITTED
    assert row.commit_complete is True
