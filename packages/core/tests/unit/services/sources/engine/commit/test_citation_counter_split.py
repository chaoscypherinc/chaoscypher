# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the split of CITATIONS_SKIPPED_NO_CHUNK_INDEX into two counters.

The old code bundled two distinct failure modes into one counter:
- Entity/relationship has no ``chunk_index`` (upstream merge collapsed it).
- ``chunk_index`` exists but does not map to a stored chunk (commit-pipeline drift).

After the split:
- ``CITATIONS_SKIPPED_NO_CHUNK_INDEX`` covers the "no chunk_index" case only.
- ``CITATIONS_SKIPPED_INDEX_NOT_MAPPED`` covers the "chunk_index present but not
  found in chunk_index_to_id" case only.

These tests call ``_create_source_citations`` and ``_create_relationship_citations``
directly with a minimal service stub so we can control exactly which failure
mode fires and assert the counters are routed correctly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.quality.counters import QualityCounter


# ---------------------------------------------------------------------------
# Minimal service factory
# ---------------------------------------------------------------------------


def _make_service() -> Any:
    """Return a SourceCommitService instance with stub dependencies.

    Only the attributes actually used by _create_source_citations and
    _create_relationship_citations are set:
    - ``sources_repository`` — needs ``create_citations_batch``,
      ``create_relationship_citations_batch``, and
      ``increment_source_counter``.
    - ``database_name`` — a plain string.
    """
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService
    from chaoscypher_core.settings import EngineSettings

    # Stub out the sources_repository — we capture counter calls via
    # increment_source_counter and silence the citation batch writes.
    repo_stub = MagicMock()
    repo_stub.create_citations_batch = MagicMock(return_value=None)
    repo_stub.create_relationship_citations_batch = MagicMock(return_value=None)

    # We'll track every (column, n) pair that the helper fires.
    bumped: list[tuple[str, int]] = []

    def _fake_increment(*, source_id: str, database_name: str, column: str, n: int = 1) -> None:
        bumped.append((column, n))

    repo_stub.increment_source_counter = _fake_increment
    repo_stub.database_name = "default"

    # The rest of the dependencies are not exercised by the two sub-methods
    # being tested here.
    graph_repository = MagicMock(spec=GraphRepository)
    settings = EngineSettings()
    search_repository = MagicMock(spec=SearchRepository)

    svc = SourceCommitService(
        graph_repository=graph_repository,
        source_repository=repo_stub,
        sources_repository=repo_stub,
        indexing_repository=MagicMock(),
        search_repository=search_repository,
        settings=settings,
    )
    svc.database_name = "default"
    return svc, bumped


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _counter_total(bumped: list[tuple[str, int]], counter: QualityCounter) -> int:
    """Sum all increments for the given counter."""
    return sum(n for col, n in bumped if col == counter.value)


# ---------------------------------------------------------------------------
# _create_source_citations — entity citations
# ---------------------------------------------------------------------------


class TestEntityCitationCounterSplit:
    """CITATIONS_SKIPPED_NO_CHUNK_INDEX and CITATIONS_SKIPPED_INDEX_NOT_MAPPED
    are routed to the right failure mode in _create_source_citations.
    """

    @pytest.mark.asyncio
    async def test_no_chunk_index_increments_only_no_chunk_index_counter(self) -> None:
        """Entity with chunk_index=None → CITATIONS_SKIPPED_NO_CHUNK_INDEX only.

        CITATIONS_SKIPPED_INDEX_NOT_MAPPED must NOT be touched.
        """
        svc, bumped = _make_service()

        # chunk_index_to_id has entry for index 0 (not relevant — entity has None)
        chunk_index_to_id = {0: "chunk-id-0"}
        entity_index_to_node_id = {0: "node-id-0"}
        entity_index_to_node = {0: MagicMock(label="Alice")}
        commit_data = {
            "entities": [
                {"name": "Alice", "type": "Person", "chunk_index": None},
            ],
        }

        await svc._create_source_citations(
            file_id="src-001",
            source_id="src-001",
            entity_index_to_node_id=entity_index_to_node_id,
            entity_index_to_node=entity_index_to_node,
            commit_data=commit_data,
            chunk_index_to_id=chunk_index_to_id,
        )

        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_NO_CHUNK_INDEX) == 1, (
            "Expected exactly 1 CITATIONS_SKIPPED_NO_CHUNK_INDEX increment for entity "
            "with chunk_index=None"
        )
        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_INDEX_NOT_MAPPED) == 0, (
            "CITATIONS_SKIPPED_INDEX_NOT_MAPPED must NOT be incremented when "
            "chunk_index is None (that is the no_chunk_index case, not the not_mapped case)"
        )

    @pytest.mark.asyncio
    async def test_chunk_index_not_mapped_increments_only_index_not_mapped_counter(self) -> None:
        """Entity with chunk_index=999 (not in stored chunks) → CITATIONS_SKIPPED_INDEX_NOT_MAPPED only.

        CITATIONS_SKIPPED_NO_CHUNK_INDEX must NOT be touched.
        """
        svc, bumped = _make_service()

        # chunk_index_to_id has entry for index 0 only; 999 is absent
        chunk_index_to_id = {0: "chunk-id-0"}
        entity_index_to_node_id = {0: "node-id-0"}
        entity_index_to_node = {0: MagicMock(label="Bob")}
        commit_data = {
            "entities": [
                {"name": "Bob", "type": "Person", "chunk_index": 999},
            ],
        }

        await svc._create_source_citations(
            file_id="src-002",
            source_id="src-002",
            entity_index_to_node_id=entity_index_to_node_id,
            entity_index_to_node=entity_index_to_node,
            commit_data=commit_data,
            chunk_index_to_id=chunk_index_to_id,
        )

        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_INDEX_NOT_MAPPED) == 1, (
            "Expected exactly 1 CITATIONS_SKIPPED_INDEX_NOT_MAPPED increment for entity "
            "with chunk_index=999 that has no matching stored chunk"
        )
        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_NO_CHUNK_INDEX) == 0, (
            "CITATIONS_SKIPPED_NO_CHUNK_INDEX must NOT be incremented when "
            "chunk_index exists but doesn't map (that is the index_not_mapped case)"
        )

    @pytest.mark.asyncio
    async def test_mixed_failures_increment_correct_counters_independently(self) -> None:
        """One entity with chunk_index=None and one with chunk_index=999.

        Each counter increments exactly once, independently.
        """
        svc, bumped = _make_service()

        chunk_index_to_id = {0: "chunk-id-0"}
        entity_index_to_node_id = {0: "node-id-0", 1: "node-id-1"}
        entity_index_to_node = {
            0: MagicMock(label="Alice"),
            1: MagicMock(label="Bob"),
        }
        commit_data = {
            "entities": [
                {"name": "Alice", "type": "Person", "chunk_index": None},  # no_chunk_index
                {"name": "Bob", "type": "Person", "chunk_index": 999},  # index_not_mapped
            ],
        }

        await svc._create_source_citations(
            file_id="src-003",
            source_id="src-003",
            entity_index_to_node_id=entity_index_to_node_id,
            entity_index_to_node=entity_index_to_node,
            commit_data=commit_data,
            chunk_index_to_id=chunk_index_to_id,
        )

        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_NO_CHUNK_INDEX) == 1
        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_INDEX_NOT_MAPPED) == 1


# ---------------------------------------------------------------------------
# _create_relationship_citations — relationship citations
# ---------------------------------------------------------------------------


class TestRelationshipCitationCounterSplit:
    """CITATIONS_SKIPPED_NO_CHUNK_INDEX and CITATIONS_SKIPPED_INDEX_NOT_MAPPED
    are routed to the right failure mode in _create_relationship_citations.
    """

    def _make_edge(
        self, source_node_id: str, target_node_id: str, label: str = "relates_to"
    ) -> Any:
        edge = MagicMock()
        edge.source_node_id = source_node_id
        edge.target_node_id = target_node_id
        edge.label = label
        edge.template_id = label
        return edge

    def _make_node(self, node_id: str, label: str) -> Any:
        node = MagicMock()
        node.id = node_id
        node.label = label
        return node

    @pytest.mark.asyncio
    async def test_no_chunk_index_rel_increments_only_no_chunk_index_counter(self) -> None:
        """Relationship with chunk_index=None → CITATIONS_SKIPPED_NO_CHUNK_INDEX only."""
        svc, bumped = _make_service()

        src_node = self._make_node("node-a", "Alpha")
        tgt_node = self._make_node("node-b", "Beta")

        edge = self._make_edge("node-a", "node-b")
        created_edges = ["edge-id-1"]
        edges_to_create = [edge]

        relationships = [
            {
                "source": 0,
                "target": 1,
                "type": "relates_to",
                "chunk_index": None,  # no chunk_index
            }
        ]
        entity_index_to_node = {0: src_node, 1: tgt_node}
        chunk_index_to_id = {0: "chunk-id-0"}

        await svc._create_relationship_citations(
            file_id="src-004",
            source_id="src-004",
            created_edges=created_edges,
            edges_to_create=edges_to_create,
            relationships=relationships,
            entity_index_to_node=entity_index_to_node,
            chunk_index_to_id=chunk_index_to_id,
        )

        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_NO_CHUNK_INDEX) == 1
        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_INDEX_NOT_MAPPED) == 0

    @pytest.mark.asyncio
    async def test_chunk_index_not_mapped_rel_increments_only_index_not_mapped_counter(
        self,
    ) -> None:
        """Relationship with chunk_index=999 (not in stored chunks) → CITATIONS_SKIPPED_INDEX_NOT_MAPPED only."""
        svc, bumped = _make_service()

        src_node = self._make_node("node-a", "Alpha")
        tgt_node = self._make_node("node-b", "Beta")

        edge = self._make_edge("node-a", "node-b")
        created_edges = ["edge-id-1"]
        edges_to_create = [edge]

        relationships = [
            {
                "source": 0,
                "target": 1,
                "type": "relates_to",
                "chunk_index": 999,  # index not in chunk_index_to_id
            }
        ]
        entity_index_to_node = {0: src_node, 1: tgt_node}
        chunk_index_to_id = {0: "chunk-id-0"}  # 999 not present

        await svc._create_relationship_citations(
            file_id="src-005",
            source_id="src-005",
            created_edges=created_edges,
            edges_to_create=edges_to_create,
            relationships=relationships,
            entity_index_to_node=entity_index_to_node,
            chunk_index_to_id=chunk_index_to_id,
        )

        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_INDEX_NOT_MAPPED) == 1
        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_NO_CHUNK_INDEX) == 0
