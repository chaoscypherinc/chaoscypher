# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""get_relationship_citations_batch: the stored evidence sentences of edges, by edge id."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from structlog.testing import capture_logs

from chaoscypher_core.adapters.sqlite import models


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _store(adapter: SqliteAdapter, *citations: models.RelationshipCitation) -> None:
    """Write ``citations`` with the source and chunk rows their foreign keys name."""
    session = adapter.session
    assert session is not None
    session.add_all(
        models.SourceRow(id=sid, database_name="db", filename="f.txt", filepath="f.txt")
        for sid in ("s1", "s2")
    )
    session.commit()  # the parents first: the unit of work does not order by a bare foreign key
    session.add_all(
        models.DocumentChunk(id=cid, database_name="db", source_id="s1", chunk_index=i, content="x")
        for i, cid in enumerate(("c1", "c2", "c9"))
    )
    session.commit()
    session.add_all(citations)
    session.commit()


def _citation(
    cid: str, edge: str, chunk: str, idx: int, text: str, *, meta: Any = True
) -> models.RelationshipCitation:
    """A relationship citation row citing sentence ``idx`` (``text``) of ``chunk``."""
    sentences = [{"text": text, "chunk_id": chunk, "local_index": idx}]
    return models.RelationshipCitation(
        id=cid,
        database_name="db",
        edge_id=edge,
        edge_label="spouse_of",
        edge_type=None,
        source_entity_label="A",
        target_entity_label="B",
        source_id="s1",
        chunk_id=chunk,
        confidence=1.0,
        extraction_method="ai_extraction",
        justification="they married",
        citation_metadata={"sent_ref": "S2", "sentences": sentences} if meta is True else meta,
    )


def test_rows_come_back_by_edge_then_chunk_with_their_sentences(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """Rows are ordered by edge id then chunk id, each with its cited sentences or ``[]``."""
    _store(
        sqlite_adapter,
        _citation("rc3", "e2", "c9", 0, "Later they parted."),
        _citation("rc1", "e1", "c2", 1, "A married B."),
        _citation("rc2", "e1", "c1", 0, "A and B wed.", meta=None),
    )
    rows = sqlite_adapter.get_relationship_citations_batch("db", ["e1", "e2"])
    assert [(r["edge_id"], r["chunk_id"]) for r in rows] == [
        ("e1", "c1"),
        ("e1", "c2"),
        ("e2", "c9"),
    ]
    assert rows[1]["sentences"] == [{"text": "A married B.", "chunk_id": "c2", "local_index": 1}]
    assert rows[0]["sentences"] == []
    assert rows[0]["justification"] == "they married"
    assert rows[0]["source_id"] == "s1"


def test_rows_of_one_chunk_come_back_by_row_id(sqlite_adapter: SqliteAdapter) -> None:
    """Two citations of one edge in one chunk keep a fixed order: the row id breaks the tie."""
    _store(
        sqlite_adapter,
        _citation("rc9", "e1", "c2", 1, "Second."),
        _citation("rc1", "e1", "c2", 0, "First."),
    )
    rows = sqlite_adapter.get_relationship_citations_batch("db", ["e1"])
    assert [r["sentences"][0]["text"] for r in rows] == ["First.", "Second."]


def test_metadata_that_is_not_a_sentence_list_keeps_the_row_with_no_sentences(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """A malformed ``citation_metadata`` never drops the citation: its row comes back, flagged in the log.

    Metadata with no ``sentences`` at all (a ``sent_ref`` alone) is well formed and not flagged.
    """
    _store(
        sqlite_adapter,
        _citation("rc1", "e1", "c1", 0, "x", meta=["not", "a", "dict"]),
        _citation("rc2", "e2", "c1", 0, "x", meta={"sent_ref": "S1", "sentences": "S1"}),
        _citation("rc3", "e3", "c1", 0, "x", meta={"sentences": ["junk", {"text": "Kept."}]}),
        _citation("rc4", "e4", "c1", 0, "x", meta={"sent_ref": "S1"}),
    )
    with capture_logs() as logs:
        rows = sqlite_adapter.get_relationship_citations_batch("db", ["e1", "e2", "e3", "e4"])
    assert [(r["edge_id"], r["sentences"]) for r in rows] == [
        ("e1", []),
        ("e2", []),
        ("e3", [{"text": "Kept."}]),
        ("e4", []),
    ]
    flagged = [
        e["edge_id"] for e in logs if e["event"] == "relationship_citation_sentences_malformed"
    ]
    assert flagged == ["e1", "e2", "e3"]


def test_unknown_edges_and_other_databases_return_nothing(sqlite_adapter: SqliteAdapter) -> None:
    """An unknown edge, another database or no edge ids at all give no rows."""
    _store(sqlite_adapter, _citation("rc1", "e1", "c2", 1, "A married B."))
    assert sqlite_adapter.get_relationship_citations_batch("db", ["nope"]) == []
    assert sqlite_adapter.get_relationship_citations_batch("other", ["e1"]) == []
    assert sqlite_adapter.get_relationship_citations_batch("db", []) == []


def test_source_scope_filters_the_rows(sqlite_adapter: SqliteAdapter) -> None:
    """``source_ids`` keeps only the citations of those sources."""
    _store(sqlite_adapter, _citation("rc1", "e1", "c2", 1, "A married B."))
    assert sqlite_adapter.get_relationship_citations_batch("db", ["e1"], source_ids=["s2"]) == []
    assert (
        len(sqlite_adapter.get_relationship_citations_batch("db", ["e1"], source_ids=["s1"])) == 1
    )
