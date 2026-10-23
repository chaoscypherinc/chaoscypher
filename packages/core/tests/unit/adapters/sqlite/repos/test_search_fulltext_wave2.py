# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The node and chunk full-text indexes stay what the graph and the chunks are (audit wave 2).

Until 2026-10-06 re-indexing a node left its old words in the FTS index as an
orphan (``INSERT OR REPLACE`` fires no delete trigger), the boot check could
not see orphans (plain ``integrity-check``), nothing rebuilt the node index
after the identity resolver merged nodes, and the chunk backfill compared row
counts only.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.models import Node
from chaoscypher_core.utils.retrieval_faults import collect_retrieval_faults
from tests.unit.adapters.sqlite.repos.conftest import open_search_repo


_repo = open_search_repo


def _node(nid: str, label: str, *aliases: str) -> Node:
    return Node(id=nid, label=label, template_id="t", properties={"aliases": list(aliases)})


def _rows(path: Path) -> dict[str, str]:
    with sqlite3.connect(path) as con:
        return dict(con.execute("SELECT node_id, properties FROM fulltext_content").fetchall())


def test_re_indexing_a_node_leaves_no_orphan_the_strict_check_would_find(db: Path) -> None:
    """An update is an upsert: the old words leave the index with the old row."""
    repo = _repo(db)
    repo.index_node(_node("n1", "Prince Andrew"))
    repo.index_node(_node("n1", "Andrew Bolkónski"))
    repo.index_nodes_batch([_node("n1", "Andrew Bolkónski", "Andrei")])
    assert repo.check_fts5_integrity() == (True, None)
    assert repo.keyword_search("Prince") == []
    assert [i for i, _ in repo.keyword_search("Andrei")] == ["n1"]


def test_the_boot_check_compares_the_index_with_its_content(db: Path) -> None:
    """An orphan of the kind INSERT OR REPLACE made is drift; the plain check passed it."""
    repo = _repo(db)
    repo.index_node(_node("n1", "Prince Andrew"))
    with sqlite3.connect(db) as con:  # the pre-2026-10-06 writer
        con.execute(
            "INSERT OR REPLACE INTO fulltext_content (node_id, label, properties, searchable_text) "
            "VALUES ('n1', 'Andrew', '{}', 'Andrew')"
        )
        con.execute("INSERT INTO fulltext_index(fulltext_index) VALUES ('integrity-check')")
    ok, error = repo.check_fts5_integrity()
    assert ok is False
    assert error


def test_rebuild_node_fulltext_holds_exactly_the_graphs_nodes_and_aliases(db: Path) -> None:
    """Rows of merged-away nodes go, a kept node's new aliases come in, orphans are dropped."""
    repo = _repo(db)
    repo.index_nodes_batch(
        [_node("anna", "Anna Pávlovna"), _node("mlle", "Mlle Schérer", "the hostess")]
    )
    with sqlite3.connect(db) as con:  # an orphan from the old writer
        con.execute(
            "INSERT OR REPLACE INTO fulltext_content (node_id, label, properties, searchable_text) "
            "VALUES ('anna', 'Anna Pávlovna', '{}', 'Anna Pávlovna')"
        )
    kept = _node("anna", "Anna Pávlovna", "Mlle Schérer", "the hostess")
    assert repo.rebuild_node_fulltext([kept]) == 1
    assert set(_rows(db)) == {"anna"}
    assert "the hostess" in _rows(db)["anna"]
    assert [i for i, _ in repo.keyword_search("hostess")] == ["anna"]
    assert repo.check_fts5_integrity() == (True, None)


def test_the_chunk_backfill_compares_content_not_counts(db: Path) -> None:
    """A chunk rewritten while the triggers were missing is re-indexed on the next open."""
    repo = _repo(db)
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO document_chunks (id, database_name, source_id, chunk_index, content, "
            "status, created_at, citation_offset_method) "
            "VALUES ('c1', 'db', 's1', 0, 'Dolokhov drank rum.', 'indexed', '2026-10-06', 'none')"
        )
        # a migration rebuilt the table: its triggers are gone, the count stays the same
        con.execute("DROP TRIGGER fulltext_chunks_from_document_au")
        con.execute("UPDATE document_chunks SET content = 'Natasha danced.' WHERE id = 'c1'")
    assert repo.keyword_search("danced", item_type="chunk") == []
    reopened = _repo(db)
    assert [i for i, _ in reopened.keyword_search("danced", item_type="chunk")] == ["chunk:c1"]
    assert reopened.keyword_search("rum", item_type="chunk") == []


def test_a_swallowed_search_failure_is_recorded_for_the_retrieval(db: Path) -> None:
    """The repository still degrades softly, but a collecting caller learns which arm failed."""
    repo = _repo(db)
    with collect_retrieval_faults() as faults:
        assert repo.vector_search([1.0, 0.0], k=3) == []  # the wrong size
    assert faults == ["vector_search:dimension_mismatch"]
    assert repo.vector_search([1.0, 0.0], k=3) == []  # outside a collector: nothing recorded


def test_a_database_holding_an_orphan_still_opens_and_says_so(db: Path) -> None:
    """The stricter check reads an orphan as corruption: boot warns, it does not crash."""
    from structlog.testing import capture_logs

    repo = _repo(db)
    repo.index_node(_node("n1", "Prince Andrew"))
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT OR REPLACE INTO fulltext_content (node_id, label, properties, searchable_text) "
            "VALUES ('n1', 'Andrew', '{}', 'Andrew')"
        )
    evict_engine(db)
    with capture_logs() as logs:
        reopened = _repo(db)
    assert any(e["event"] == "search_index_drift_detected" for e in logs)
    reopened.rebuild_node_fulltext([_node("n1", "Andrew")])
    assert reopened.check_fts5_integrity() == (True, None)
