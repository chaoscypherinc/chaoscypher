# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the split of CITATIONS_SKIPPED_NO_CHUNK_INDEX into two counters.

Citations name their chunk directly: aggregation stamps ``cited_chunk_id`` and
``evidence`` on every extracted item, and the commit only reads them. Two
distinct failure modes keep two counters:
- ``CITATIONS_SKIPPED_NO_CHUNK_INDEX``: the item carries no ``cited_chunk_id``.
- ``CITATIONS_SKIPPED_INDEX_NOT_MAPPED``: the ``cited_chunk_id`` is not one of
  the source's stored chunks (commit-pipeline drift).

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
        """Entity with cited_chunk_id=None → CITATIONS_SKIPPED_NO_CHUNK_INDEX only.

        CITATIONS_SKIPPED_INDEX_NOT_MAPPED must NOT be touched.
        """
        svc, bumped = _make_service()

        # chunk-id-0 is stored (not relevant — entity cites no chunk)
        stored_chunk_ids = {"chunk-id-0"}
        entity_index_to_node_id = {0: "node-id-0"}
        entity_index_to_node = {0: MagicMock(label="Alice")}
        commit_data = {
            "entities": [
                {"name": "Alice", "type": "Person", "cited_chunk_id": None},
            ],
        }

        await svc._create_source_citations(
            file_id="src-001",
            source_id="src-001",
            entity_index_to_node_id=entity_index_to_node_id,
            entity_index_to_node=entity_index_to_node,
            commit_data=commit_data,
            stored_chunk_ids=stored_chunk_ids,
        )

        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_NO_CHUNK_INDEX) == 1, (
            "Expected exactly 1 CITATIONS_SKIPPED_NO_CHUNK_INDEX increment for entity "
            "with cited_chunk_id=None"
        )
        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_INDEX_NOT_MAPPED) == 0, (
            "CITATIONS_SKIPPED_INDEX_NOT_MAPPED must NOT be incremented when "
            "cited_chunk_id is None (that is the no_chunk_index case, not the not_mapped case)"
        )

    @pytest.mark.asyncio
    async def test_chunk_index_not_mapped_increments_only_index_not_mapped_counter(self) -> None:
        """Entity with cited_chunk_id="chunk-id-999" (not in stored chunks) → CITATIONS_SKIPPED_INDEX_NOT_MAPPED only.

        CITATIONS_SKIPPED_NO_CHUNK_INDEX must NOT be touched.
        """
        svc, bumped = _make_service()

        # only chunk-id-0 is stored; chunk-id-999 is absent
        stored_chunk_ids = {"chunk-id-0"}
        entity_index_to_node_id = {0: "node-id-0"}
        entity_index_to_node = {0: MagicMock(label="Bob")}
        commit_data = {
            "entities": [
                {"name": "Bob", "type": "Person", "cited_chunk_id": "chunk-id-999"},
            ],
        }

        await svc._create_source_citations(
            file_id="src-002",
            source_id="src-002",
            entity_index_to_node_id=entity_index_to_node_id,
            entity_index_to_node=entity_index_to_node,
            commit_data=commit_data,
            stored_chunk_ids=stored_chunk_ids,
        )

        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_INDEX_NOT_MAPPED) == 1, (
            "Expected exactly 1 CITATIONS_SKIPPED_INDEX_NOT_MAPPED increment for entity "
            "with cited_chunk_id='chunk-id-999' that has no matching stored chunk"
        )
        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_NO_CHUNK_INDEX) == 0, (
            "CITATIONS_SKIPPED_NO_CHUNK_INDEX must NOT be incremented when "
            "cited_chunk_id exists but is not stored (that is the index_not_mapped case)"
        )

    @pytest.mark.asyncio
    async def test_mixed_failures_increment_correct_counters_independently(self) -> None:
        """One entity with cited_chunk_id=None and one with cited_chunk_id="chunk-id-999".

        Each counter increments exactly once, independently.
        """
        svc, bumped = _make_service()

        stored_chunk_ids = {"chunk-id-0"}
        entity_index_to_node_id = {0: "node-id-0", 1: "node-id-1"}
        entity_index_to_node = {
            0: MagicMock(label="Alice"),
            1: MagicMock(label="Bob"),
        }
        commit_data = {
            "entities": [
                {"name": "Alice", "type": "Person", "cited_chunk_id": None},  # no_chunk_index
                {
                    "name": "Bob",
                    "type": "Person",
                    "cited_chunk_id": "chunk-id-999",
                },  # index_not_mapped
            ],
        }

        await svc._create_source_citations(
            file_id="src-003",
            source_id="src-003",
            entity_index_to_node_id=entity_index_to_node_id,
            entity_index_to_node=entity_index_to_node,
            commit_data=commit_data,
            stored_chunk_ids=stored_chunk_ids,
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
        self,
        source_node_id: str,
        target_node_id: str,
        label: str = "relates_to",
        properties: dict[str, Any] | None = None,
    ) -> Any:
        edge = MagicMock()
        edge.source_node_id = source_node_id
        edge.target_node_id = target_node_id
        edge.label = label
        edge.template_id = label
        # Real EdgeCreate.properties is always a dict; model it faithfully so
        # ``edge.properties.get("inverse_of")`` behaves like production (a bare
        # MagicMock attribute would be truthy and mis-classify every edge).
        edge.properties = properties if properties is not None else {}
        return edge

    def _make_node(self, node_id: str, label: str) -> Any:
        node = MagicMock()
        node.id = node_id
        node.label = label
        return node

    @pytest.mark.asyncio
    async def test_no_chunk_index_rel_increments_only_no_chunk_index_counter(self) -> None:
        """Relationship with cited_chunk_id=None → CITATIONS_SKIPPED_NO_CHUNK_INDEX only."""
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
                "cited_chunk_id": None,  # no cited chunk
            }
        ]
        entity_index_to_node = {0: src_node, 1: tgt_node}
        stored_chunk_ids = {"chunk-id-0"}

        await svc._create_relationship_citations(
            file_id="src-004",
            source_id="src-004",
            created_edges=created_edges,
            edges_to_create=edges_to_create,
            relationships=relationships,
            entity_index_to_node=entity_index_to_node,
            stored_chunk_ids=stored_chunk_ids,
        )

        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_NO_CHUNK_INDEX) == 1
        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_INDEX_NOT_MAPPED) == 0

    @pytest.mark.asyncio
    async def test_chunk_index_not_mapped_rel_increments_only_index_not_mapped_counter(
        self,
    ) -> None:
        """Relationship with cited_chunk_id="chunk-id-999" (not in stored chunks) → CITATIONS_SKIPPED_INDEX_NOT_MAPPED only."""
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
                "cited_chunk_id": "chunk-id-999",  # not a stored chunk
            }
        ]
        entity_index_to_node = {0: src_node, 1: tgt_node}
        stored_chunk_ids = {"chunk-id-0"}  # chunk-id-999 not present

        await svc._create_relationship_citations(
            file_id="src-005",
            source_id="src-005",
            created_edges=created_edges,
            edges_to_create=edges_to_create,
            relationships=relationships,
            entity_index_to_node=entity_index_to_node,
            stored_chunk_ids=stored_chunk_ids,
        )

        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_INDEX_NOT_MAPPED) == 1
        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_NO_CHUNK_INDEX) == 0

    @pytest.mark.asyncio
    async def test_inverse_edge_is_not_counted_as_citation_skip(self) -> None:
        """Synthetic inverse edges must not inflate the skipped counter.

        Inverse edges (tagged ``inverse_of``) mirror a forward relationship and
        carry no independent chunk provenance. Their reversed (src, tgt) pair is
        never present in ``node_pair_to_rel``, so the old loop counted every
        inverse edge as CITATIONS_SKIPPED_NO_CHUNK_INDEX — one phantom skip per
        inverse edge on the Data Quality tab. They are now skipped outright.
        """
        svc, bumped = _make_service()

        src_node = self._make_node("node-a", "Alpha")
        tgt_node = self._make_node("node-b", "Beta")

        # A forward edge with a valid cited_chunk_id (earns a real citation) plus
        # its synthetic inverse (swapped nodes, tagged inverse_of).
        forward = self._make_edge("node-a", "node-b")
        inverse = self._make_edge("node-b", "node-a", properties={"inverse_of": "relates_to"})
        created_edges = ["edge-fwd", "edge-inv"]
        edges_to_create = [forward, inverse]

        relationships = [
            {"source": 0, "target": 1, "type": "relates_to", "cited_chunk_id": "chunk-id-0"},
        ]
        entity_index_to_node = {0: src_node, 1: tgt_node}
        stored_chunk_ids = {"chunk-id-0"}

        await svc._create_relationship_citations(
            file_id="src-006",
            source_id="src-006",
            created_edges=created_edges,
            edges_to_create=edges_to_create,
            relationships=relationships,
            entity_index_to_node=entity_index_to_node,
            stored_chunk_ids=stored_chunk_ids,
        )

        # The inverse edge is skipped silently — neither counter fires for it,
        # and the forward edge earned a real citation (also no skip).
        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_NO_CHUNK_INDEX) == 0
        assert _counter_total(bumped, QualityCounter.CITATIONS_SKIPPED_INDEX_NOT_MAPPED) == 0


# ---------------------------------------------------------------------------
# Citation evidence: the commit reads cited_chunk_id + evidence
# ---------------------------------------------------------------------------


def _captured(stub_method: Any) -> list[dict[str, Any]]:
    """Return the citation dicts passed to a stubbed ``create_*_batch`` call."""
    stub_method.assert_called_once()
    return stub_method.call_args.args[0]


class TestCitationEvidence:
    """Citations carry the cited sentences and the chunk they live in."""

    def _edge(self, src: str, tgt: str, label: str) -> Any:
        edge = MagicMock()
        edge.source_node_id = src
        edge.target_node_id = tgt
        edge.label = label
        edge.template_id = f"tpl-{label}"
        edge.properties = {}
        return edge

    def _node(self, node_id: str, label: str) -> Any:
        node = MagicMock()
        node.id = node_id
        node.label = label
        return node

    @pytest.mark.asyncio
    async def test_entity_citation_snippet_is_the_cited_chunk_sentences(self) -> None:
        """``context_snippet`` comes from evidence in the cited chunk only."""
        svc, _bumped = _make_service()
        evidence = [
            {"text": "Anna met Pierre.", "chunk_id": "c1", "local_index": 0},
            {"text": "Anna left.", "chunk_id": "c2", "local_index": 3},
        ]
        await svc._create_source_citations(
            file_id="src-e",
            source_id="src-e",
            entity_index_to_node_id={0: "node-a"},
            entity_index_to_node={0: MagicMock(label="Anna")},
            commit_data={
                "entities": [
                    {
                        "name": "Anna",
                        "type": "Person",
                        # A group index that must never be read as a chunk index.
                        "chunk_index": 0,
                        "sent_ref": "S2, S9",
                        "cited_chunk_id": "c1",
                        "evidence": evidence,
                    }
                ]
            },
            stored_chunk_ids={"c0", "c1", "c2"},
        )
        # One row per evidence chunk (audit W2-13), each with its own chunk's sentences;
        # the group-level sent_ref rides on the cited chunk's row.
        citation, other = _captured(svc.sources_repository.create_citations_batch)
        assert citation["chunk_id"] == "c1"
        assert citation["context_snippet"] == "Anna met Pierre."
        assert citation["citation_metadata"] == {"sent_ref": "S2, S9", "sentences": evidence[:1]}
        assert other["chunk_id"] == "c2"
        assert other["context_snippet"] == "Anna left."
        assert other["citation_metadata"] == {"sent_ref": None, "sentences": evidence[1:]}

    @pytest.mark.asyncio
    async def test_two_edge_types_on_one_node_pair_keep_their_own_evidence(self) -> None:
        """Edges sharing endpoints are matched by type, not last-write-wins."""
        svc, bumped = _make_service()
        knows_ev = [{"text": "Anna knows Pierre.", "chunk_id": "c0", "local_index": 1}]
        meets_ev = [{"text": "Anna met Pierre at dawn.", "chunk_id": "c3", "local_index": 0}]
        relationships = [
            {
                "source": 0,
                "target": 1,
                "type": "knows",
                "sent_ref": "S2",
                "cited_chunk_id": "c0",
                "evidence": knows_ev,
            },
            {
                "source": 0,
                "target": 1,
                "type": "Meets",
                "sent_ref": "S7",
                "cited_chunk_id": "c3",
                "evidence": meets_ev,
            },
        ]
        await svc._create_relationship_citations(
            file_id="src-r",
            source_id="src-r",
            created_edges=["edge-knows", "edge-meets"],
            edges_to_create=[
                self._edge("node-a", "node-b", "knows"),
                self._edge("node-a", "node-b", "meets"),
            ],
            relationships=relationships,
            entity_index_to_node={
                0: self._node("node-a", "Anna"),
                1: self._node("node-b", "Pierre"),
            },
            stored_chunk_ids={"c0", "c3"},
        )
        by_edge = {
            c["edge_id"]: c
            for c in _captured(svc.sources_repository.create_relationship_citations_batch)
        }
        assert by_edge["edge-knows"]["chunk_id"] == "c0"
        assert by_edge["edge-knows"]["citation_metadata"] == {
            "sent_ref": "S2",
            "sentences": knows_ev,
        }
        assert by_edge["edge-meets"]["chunk_id"] == "c3"
        assert by_edge["edge-meets"]["citation_metadata"]["sentences"][0]["text"] == (
            "Anna met Pierre at dawn."
        )
        assert bumped == []
