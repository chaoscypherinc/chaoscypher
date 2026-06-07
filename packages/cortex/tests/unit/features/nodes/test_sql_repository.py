# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SqlNodeRepository SQLModel query builders.

These exercise the real SQL emitted by ``get_citations_for_node``,
``get_source_id_for_node``, ``get_connected_nodes``, and
``get_node_stats_batch`` against a live in-memory SQLite database with the
SQLModel tables created. Rows are seeded directly (FK enforcement is off by
default for SQLite, so only the rows under test need to exist).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine

from chaoscypher_core.adapters.sqlite.models import (
    DocumentChunk,
    GraphEdge,
    GraphNode,
    SourceCitation,
    SourceRow,
)
from chaoscypher_cortex.features.nodes.sql_repository import SqlNodeRepository


_DB = "default"
_NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def session():
    """Yield a live in-memory SQLModel session with all tables created."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


def _repo(session: Session, **kwargs) -> SqlNodeRepository:
    return SqlNodeRepository(session=session, database_name=_DB, **kwargs)


def _source(source_id: str = "src-1", title: str | None = "My Source") -> SourceRow:
    return SourceRow(
        id=source_id,
        database_name=_DB,
        filename="doc.pdf",
        filepath="/tmp/doc.pdf",
        title=title,
        source_type="pdf",
        origin_url="http://example.com",
    )


def _chunk(chunk_id: str = "chunk-1", source_id: str = "src-1") -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        database_name=_DB,
        source_id=source_id,
        chunk_index=0,
        content="some chunk content",
        page_number=3,
        section="intro",
        chunk_metadata={"k": "v"},
    )


def _citation(
    citation_id: str,
    entity_uri: str,
    source_id: str = "src-1",
    chunk_id: str = "chunk-1",
    created_at: datetime | None = None,
    database_name: str = _DB,
) -> SourceCitation:
    return SourceCitation(
        id=citation_id,
        database_name=database_name,
        entity_uri=entity_uri,
        entity_label="Alice",
        source_id=source_id,
        chunk_id=chunk_id,
        confidence=0.9,
        extraction_method="ai_extraction",
        context_snippet="…Alice…",
        created_at=created_at or _NOW,
    )


def _node(node_id: str, label: str = "Alice", template_id: str = "tpl-1") -> GraphNode:
    return GraphNode(
        id=node_id,
        database_name=_DB,
        graph_name="knowledge",
        template_id=template_id,
        label=label,
    )


def _edge(
    edge_id: str,
    source_node_id: str,
    target_node_id: str,
    label: str = "knows",
    template_id: str = "rel-tpl",
    database_name: str = _DB,
) -> GraphEdge:
    return GraphEdge(
        id=edge_id,
        database_name=database_name,
        graph_name="knowledge",
        template_id=template_id,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        label=label,
    )


# ---------------------------------------------------------------------------
# get_citations_for_node
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetCitationsForNode:
    def test_returns_joined_rows_and_total(self, session: Session) -> None:
        session.add(_source("src-1"))
        session.add(_chunk("chunk-1", "src-1"))
        session.add(_citation("cit-1", entity_uri="node-1"))
        session.commit()

        repo = _repo(session)
        results, total = repo.get_citations_for_node("node-1")

        assert total == 1
        assert len(results) == 1
        citation, source, chunk = results[0]
        assert citation.id == "cit-1"
        assert source.id == "src-1"
        assert chunk.id == "chunk-1"
        assert chunk.content == "some chunk content"

    def test_pagination_offset_and_limit(self, session: Session) -> None:
        session.add(_source("src-1"))
        session.add(_chunk("chunk-1", "src-1"))
        # Three citations with increasing created_at so ordering is deterministic.
        for i in range(3):
            ts = datetime(2026, 1, 1 + i, tzinfo=UTC)
            session.add(_citation(f"cit-{i}", entity_uri="node-1", created_at=ts))
        session.commit()

        repo = _repo(session)
        # ordered by created_at DESC -> cit-2, cit-1, cit-0
        page1, total = repo.get_citations_for_node("node-1", offset=0, limit=2)
        assert total == 3
        assert [c.id for c, _s, _ch in page1] == ["cit-2", "cit-1"]

        page2, total2 = repo.get_citations_for_node("node-1", offset=2, limit=2)
        assert total2 == 3
        assert [c.id for c, _s, _ch in page2] == ["cit-0"]

    def test_filters_by_database_name(self, session: Session) -> None:
        session.add(_source("src-1"))
        session.add(_chunk("chunk-1", "src-1"))
        # Citation belongs to a different database -> excluded.
        session.add(_citation("cit-other", entity_uri="node-1", database_name="other-db"))
        session.commit()

        repo = _repo(session)
        results, total = repo.get_citations_for_node("node-1")
        assert total == 0
        assert results == []


# ---------------------------------------------------------------------------
# get_source_id_for_node
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSourceIdForNode:
    def test_returns_none_when_definition_not_source_document(self, session: Session) -> None:
        repo = _repo(session)
        assert (
            repo.get_source_id_for_node(
                node_id="n1", node_label="Alice", node_definition="A person"
            )
            is None
        )

    def test_returns_none_when_definition_missing(self, session: Session) -> None:
        repo = _repo(session)
        assert (
            repo.get_source_id_for_node(node_id="n1", node_label="Alice", node_definition=None)
            is None
        )

    def test_returns_source_id_when_title_in_label(self, session: Session) -> None:
        session.add(_source("src-99", title="Annual Report"))
        session.commit()

        repo = _repo(session)
        source_id = repo.get_source_id_for_node(
            node_id="n1",
            node_label="Document: Annual Report 2026",
            node_definition="Source document: uploaded by user",
        )
        assert source_id == "src-99"

    def test_returns_none_when_title_not_in_label(self, session: Session) -> None:
        session.add(_source("src-99", title="Annual Report"))
        session.commit()

        repo = _repo(session)
        source_id = repo.get_source_id_for_node(
            node_id="n1",
            node_label="Completely unrelated label",
            node_definition="Source document: uploaded by user",
        )
        assert source_id is None


# ---------------------------------------------------------------------------
# get_node_stats_batch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetNodeStatsBatch:
    def test_empty_input_returns_empty_dict(self, session: Session) -> None:
        repo = _repo(session)
        assert repo.get_node_stats_batch([]) == {}

    def test_counts_edges_citations_and_rel_types(self, session: Session) -> None:
        # node-A: 1 outgoing (to B, template rel-a), 1 incoming (from C, template rel-b)
        session.add(_edge("e1", "node-A", "node-B", template_id="rel-a"))
        session.add(_edge("e2", "node-C", "node-A", template_id="rel-b"))
        # A second edge with same template as e1 on outgoing -> still 1 distinct type extra
        session.add(_edge("e3", "node-A", "node-D", template_id="rel-a"))
        # citations for node-A
        session.add(_source("src-1"))
        session.add(_chunk("chunk-1", "src-1"))
        session.add(_citation("cit-1", entity_uri="node-A"))
        session.add(_citation("cit-2", entity_uri="node-A"))
        session.commit()

        repo = _repo(session)
        stats = repo.get_node_stats_batch(["node-A", "node-Z"])

        a = stats["node-A"]
        assert a["incoming_edge_count"] == 1
        assert a["outgoing_edge_count"] == 2
        assert a["edge_count"] == 3
        assert a["citation_count"] == 2
        # distinct templates touching node-A: rel-a, rel-b -> 2
        assert a["relationship_type_count"] == 2

        # node-Z has no data -> all zeros (initialized)
        z = stats["node-Z"]
        assert z == {
            "incoming_edge_count": 0,
            "outgoing_edge_count": 0,
            "edge_count": 0,
            "citation_count": 0,
            "relationship_type_count": 0,
        }


# ---------------------------------------------------------------------------
# get_connected_nodes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetConnectedNodes:
    def test_no_edges_returns_empty(self, session: Session) -> None:
        repo = _repo(session)
        results, total = repo.get_connected_nodes("node-A")
        assert results == []
        assert total == 0

    def test_returns_connected_with_details_and_direction(self, session: Session) -> None:
        # node-A -> node-B (outgoing), node-C -> node-A (incoming)
        session.add(_edge("e1", "node-A", "node-B", label="knows"))
        session.add(_edge("e2", "node-C", "node-A", label="reports_to"))
        session.add(_node("node-B", label="Bob", template_id="Person"))
        session.add(_node("node-C", label="Carol", template_id="Person"))
        session.commit()

        repo = _repo(session)
        results, total = repo.get_connected_nodes("node-A", sort_by="label")
        assert total == 2
        by_id = {r["id"]: r for r in results}

        assert by_id["node-B"]["direction"] == "outgoing"
        assert by_id["node-B"]["relationship"] == "knows"
        assert by_id["node-B"]["label"] == "Bob"
        assert by_id["node-B"]["template_id"] == "Person"

        assert by_id["node-C"]["direction"] == "incoming"
        assert by_id["node-C"]["relationship"] == "reports_to"
        # label sort: Bob before Carol
        assert [r["id"] for r in results] == ["node-B", "node-C"]

    def test_unknown_node_details_fall_back(self, session: Session) -> None:
        # Edge to node-B but no GraphNode row for it -> Unknown/unknown.
        session.add(_edge("e1", "node-A", "node-B", label="knows"))
        session.commit()

        repo = _repo(session)
        results, _total = repo.get_connected_nodes("node-A")
        assert results[0]["label"] == "Unknown"
        assert results[0]["template_id"] == "unknown"

    def test_edge_count_aggregates_total_edges_and_sort_by_edge_count(
        self, session: Session
    ) -> None:
        # node-A connects to B and C.
        session.add(_edge("e1", "node-A", "node-B", label="knows"))
        session.add(_edge("e2", "node-A", "node-C", label="knows"))
        # Give node-C extra unrelated edges so its total edge_count is higher.
        session.add(_edge("e3", "node-C", "node-X", label="x"))
        session.add(_edge("e4", "node-Y", "node-C", label="y"))
        session.add(_node("node-B", label="Bob"))
        session.add(_node("node-C", label="Carol"))
        session.commit()

        repo = _repo(session)
        results, total = repo.get_connected_nodes("node-A", sort_by="edge_count")
        assert total == 2
        # node-C total edges (e2,e3,e4)=3 > node-B (e1)=1, so C first.
        assert results[0]["id"] == "node-C"
        assert results[0]["edge_count"] == 3
        assert results[1]["id"] == "node-B"
        assert results[1]["edge_count"] == 1

    def test_sort_by_relationship(self, session: Session) -> None:
        session.add(_edge("e1", "node-A", "node-B", label="zeta"))
        session.add(_edge("e2", "node-A", "node-C", label="alpha"))
        session.add(_node("node-B", label="Bob"))
        session.add(_node("node-C", label="Carol"))
        session.commit()

        repo = _repo(session)
        results, _total = repo.get_connected_nodes("node-A", sort_by="relationship")
        assert [r["relationship"] for r in results] == ["alpha", "zeta"]

    def test_pagination_offset_and_limit(self, session: Session) -> None:
        for i in range(5):
            session.add(_edge(f"e{i}", "node-A", f"node-{i}", label="knows"))
            session.add(_node(f"node-{i}", label=f"N{i}"))
        session.commit()

        repo = _repo(session)
        page, total = repo.get_connected_nodes("node-A", sort_by="label", offset=2, limit=2)
        assert total == 5
        assert len(page) == 2
        assert [r["id"] for r in page] == ["node-2", "node-3"]
