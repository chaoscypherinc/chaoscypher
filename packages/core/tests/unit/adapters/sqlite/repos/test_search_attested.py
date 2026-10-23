# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The search and the attested-by predicates read membership (#795, D7).

A node merged from two sources is homed at one of them and attested by both
(``graph_node_sources``). Its home source being switched off, or a scope
naming only its other member, must not hide it: the search arms, the scope
count and the graph listings all read the one predicate in
:mod:`chaoscypher_core.adapters.sqlite.repos.source_visibility`, and the
Python forms in :mod:`chaoscypher_core.ports.search` read alike.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos.graph import GraphRepository
from chaoscypher_core.adapters.sqlite.repos.source_visibility import (
    attested_visible_sql,
    in_source_scope_sql,
)
from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
from chaoscypher_core.models import Node
from chaoscypher_core.ports.search import item_in_source_scope, item_visible
from tests.unit.adapters.sqlite.repos.conftest import open_search_repo
from tests.unit.adapters.sqlite.repos.test_search_wave6 import _graph_node, _set_enabled, _source


def _member(path: Path, nid: str, sid: str) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO graph_node_sources (node_id, source_id, snapshot) VALUES (?, ?, '{}')",
            (nid, sid),
        )


_MEMBERS: dict[str, tuple[str | None, list[str]]] = {
    "merged": ("b", ["a", "b"]),
    "only_a": ("a", ["a"]),
    "only_b": ("b", ["b"]),
    "legacy_home": ("b", []),
    "unsourced": (None, []),
}
"""Each node's home source and its members: merged, single-source, a home with no member row, none."""


@pytest.fixture
def merged(db: Path) -> tuple[Path, Any]:
    """Sources ``a`` and ``b``; the nodes of :data:`_MEMBERS`, indexed and embedded."""
    _source(db, "a")
    _source(db, "b")
    for nid, (home, members) in _MEMBERS.items():
        _graph_node(db, nid, home, f"Kessel {nid}")
        for sid in members:
            _member(db, nid, sid)
    repo = open_search_repo(db)
    repo.index_nodes_batch(
        [
            Node(id=nid, label=f"Kessel {nid}", template_id="t", source_id=home)
            for nid, (home, _) in _MEMBERS.items()
        ]
    )
    repo.index_embeddings_batch(
        [(nid, [1.0, 0.1 * i, 0.0, 0.0]) for i, nid in enumerate(_MEMBERS)], item_type="node"
    )
    return db, repo


def _ids(value: object) -> str:
    """A stable test id: a set's members sorted (xdist workers must collect the same ids)."""
    if value is None:
        return "unscoped"
    return "+".join(sorted(value)) or "none"  # type: ignore[call-overload]


def _expected(scope: list[str] | None, disabled: set[str]) -> set[str]:
    """The Python forms' reading of which nodes answer in ``scope`` with ``disabled`` off."""
    rows = {
        nid: SimpleNamespace(source_id=home, member_source_ids=members)
        for nid, (home, members) in _MEMBERS.items()
    }
    return {
        nid
        for nid, row in rows.items()
        if item_visible(row, disabled) and (scope is None or item_in_source_scope(row, scope))
    }


@pytest.mark.parametrize("disabled", [set(), {"b"}, {"a"}, {"a", "b"}], ids=_ids)
@pytest.mark.parametrize("scope", [None, ["a"], ["b"]], ids=_ids)
def test_search_listing_and_python_agree_on_merged_nodes(
    merged: tuple[Path, Any], scope: list[str] | None, disabled: set[str]
) -> None:
    db, repo = merged
    for sid in disabled:
        _set_enabled(db, sid, enabled=False)
    graph = GraphRepository(SafeSession(get_engine(str(db))), "db")
    expected = _expected(scope, disabled)

    keyword = {i for i, _ in repo.keyword_search("Kessel", 20, "node", scope)}
    vector = {i for i, _ in repo.vector_search([1.0, 0.0, 0.0, 0.0], 20, "node", scope)}
    listed = {n.id for n in graph.list_nodes_minimal(limit=100, source_ids=scope)}

    assert keyword == vector == listed == expected
    assert repo.count_in_scope("node", scope) == len(expected)


def test_a_merged_node_answers_while_any_member_is_on(merged: tuple[Path, Any]) -> None:
    """Review Focus 5 in the search: home switched off, the other member on, the node answers."""
    db, repo = merged
    _set_enabled(db, "b", enabled=False)
    assert "merged" in {i for i, _ in repo.keyword_search("Kessel", 20, "node")}
    assert "merged" in {i for i, _ in repo.keyword_search("Kessel", 20, "node", ["a"])}
    _set_enabled(db, "a", enabled=False)
    assert "merged" not in {i for i, _ in repo.keyword_search("Kessel", 20, "node")}


@pytest.mark.parametrize("nid", list(_MEMBERS))
@pytest.mark.parametrize("disabled", [set(), {"b"}, {"a", "b"}], ids=_ids)
def test_the_sql_forms_read_as_the_python_ones(
    merged: tuple[Path, Any], nid: str, disabled: set[str]
) -> None:
    """``attested_visible_sql``/``in_source_scope_sql`` and ``item_visible``/``item_in_source_scope``."""
    db, _ = merged
    for sid in disabled:
        _set_enabled(db, sid, enabled=False)
    home, members = _MEMBERS[nid]
    row = SimpleNamespace(source_id=home, member_source_ids=members)
    with get_engine(str(db)).connect() as conn:
        visible, in_a = conn.execute(
            text(
                f"SELECT {attested_visible_sql('graph_nodes', 'n')}, "
                f"{in_source_scope_sql('graph_nodes', '(:a)', 'n')} "
                "FROM graph_nodes n WHERE n.id = :id"
            ),
            {"id": nid, "a": "a"},
        ).one()
    assert bool(visible) is item_visible(row, disabled)
    assert bool(in_a) is item_in_source_scope(row, ["a"])
