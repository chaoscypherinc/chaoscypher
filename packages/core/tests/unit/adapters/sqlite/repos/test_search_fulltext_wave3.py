# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Both full-text indexes are checked on open, and an unchanged database is not rehashed (audit wave 3).

Until 2026-10-06 only the chunk index was compared with what it indexes, so a
node index left stale by a writer that skipped the search repository (a
snapshot copied in, the resolver before wave 2) served dead names to keyword
seeds; and the chunk comparison hashed every chunk on every open (0.77 s on a
179,000-chunk database).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.adapters.sqlite.repos import search as search_module
from tests.unit.adapters.sqlite.repos.conftest import (
    add_chunk,
    add_graph_node,
    keyword,
    open_search_repo,
)


_repo = open_search_repo
_add_graph_node = add_graph_node
_add_chunk = add_chunk
_keyword = keyword


def test_a_stale_node_index_is_rebuilt_from_the_graph_on_open(db: Path) -> None:
    """Nodes renamed, merged away or never indexed: the next open makes the index the graph."""
    _add_graph_node(db, "n1", "Corvin Halváne", ["Corvin"])
    _add_graph_node(db, "n2", "Odile Ferrant", ["the little princess"])
    repo = _repo(db)
    assert _keyword(repo, "Corvin") == ["n1"]  # never indexed: built on open
    with sqlite3.connect(db) as con:  # a resolver that merged n2 away and renamed n1
        con.execute("DELETE FROM graph_nodes WHERE id = 'n2'")
        con.execute(
            "UPDATE graph_nodes SET label = 'Prince Corvin', updated_at = '2026-10-07' "
            "WHERE id = 'n1'"
        )
    assert _keyword(repo, "Odile") == ["n2"]  # the open repository still serves the old rows
    reopened = _repo(db)
    assert _keyword(reopened, "Odile") == []
    assert _keyword(reopened, "Prince") == ["n1"]
    assert reopened.check_fts5_integrity() == (True, None)


def test_an_unchanged_database_is_not_rehashed_on_open(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second open finds both stamps as the last check left them: no chunk hashed, no node row derived.

    The node stamp is itself a digest of the stored rows (wave 4: a same-length
    edit slipped past counts and lengths), so the open hashes the two node
    tables once each and nothing more.
    """
    _add_graph_node(db, "n1", "Corvin Halváne", ["Corvin"])
    _repo(db)
    _add_chunk(db, "c1", "Corvin rode to Kessel.")  # through the triggers
    _repo(db)  # the chunk table moved: checked once more, in step
    hashed: list[int] = []
    derived: list[int] = []
    original = search_module._rows_digest
    original_row = search_module.node_fulltext_row

    def counting(rows: Any) -> str:
        hashed.append(1)
        return original(rows)

    def deriving(node: Any) -> dict[str, str]:
        derived.append(1)
        return original_row(node)

    monkeypatch.setattr(search_module, "_rows_digest", counting)
    monkeypatch.setattr(search_module, "node_fulltext_row", deriving)
    reopened = _repo(db)
    assert hashed == [1, 1]  # the node stamp's two digests; the chunk tables by their stamps
    assert derived == []
    assert _keyword(reopened, "Kessel", "chunk") == ["chunk:c1"]


def test_missing_chunk_triggers_force_the_full_comparison(db: Path) -> None:
    """A stamp that matches does not excuse a database whose triggers were gone."""
    _add_chunk(db, "c1", "Dolokhov drank rum.")
    _repo(db)
    with sqlite3.connect(db) as con:
        con.execute("DROP TRIGGER fulltext_chunks_from_document_au")
        # same length, same count, same rowid: the stamp cannot see this edit
        con.execute("UPDATE document_chunks SET content = 'Natasha danced.....' WHERE id = 'c1'")
    reopened = _repo(db)
    assert _keyword(reopened, "danced", "chunk") == ["chunk:c1"]
    assert _keyword(reopened, "rum", "chunk") == []


def test_a_database_without_graph_nodes_keeps_its_node_index(db: Path) -> None:
    """An index written on its own (no graph rows to compare with) is left as it is."""
    from chaoscypher_core.models import Node

    repo = _repo(db)
    repo.index_node(Node(id="n1", label="Corvin", template_id="t", properties={}))
    assert _keyword(_repo(db), "Corvin") == ["n1"]
