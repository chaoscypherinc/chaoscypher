# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Supersession on single-valued relations (spec 2026-10-20 decision 6), over real repositories.

A memory source states facts (edges it attests). On a single-valued relation the later
memory closes the earlier fact with its own date; both are kept. A document's fact has no
date and is never closed; the same fact twice closes nothing; an older arrival closes itself.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.models import EdgeCreate, NodeCreate, TemplateCreate
from chaoscypher_core.services.graph.management.release import rebuild_from_members
from chaoscypher_core.services.graph.memory.supersession import (
    MEMORY_SINGLE_VALUED,
    Superseded,
    supersede_memory_edges,
)
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DEFAULT_NAME_CONVENTIONS,
)


DB = "test"
MEMORIES = {
    # id: (by, valid_from)
    "m_paris": ("alice", "2026-03-02"),
    "m_lyon": ("alice", "2026-10-20"),
    "m_old": ("alice", "2026-01-01"),
    "m_same_day": ("bob", "2026-10-20"),
}


def _source(sid: str, by: str | None, valid_from: str | None, created: datetime) -> SourceRow:
    return SourceRow(
        id=sid,
        database_name=DB,
        filename=f"{sid}.txt",
        filepath=f"/{sid}.txt",
        file_type="txt",
        status="committed",
        source_type="memory" if valid_from else "file",
        memory_by=by,
        valid_from=valid_from,
        created_at=created,
    )


@pytest.fixture
def repo(tmp_path: Path) -> GraphRepository:
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(db_path), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name=DB)
    adapter.connect()
    session = adapter.session
    assert session is not None
    base = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
    for i, (sid, (by, valid_from)) in enumerate(MEMORIES.items()):
        session.add(_source(sid, by, valid_from, base + timedelta(minutes=i)))
    session.add(_source("doc", None, None, base))
    session.commit()
    graph = GraphRepository(session, database_name=DB)
    graph.create_template(TemplateCreate(name="Person", template_type="node"), custom_id="person")
    graph.create_template(TemplateCreate(name="rel", template_type="edge"), custom_id="rel")
    for nid, label in (
        ("pierre", "Pierre"),
        ("paris", "Paris"),
        ("lyon", "Lyon"),
        ("anna", "Anna"),
        ("boris", "Boris"),
    ):
        graph.create_node(NodeCreate(template_id="person", label=label), custom_id=nid)
    return graph


def _fact(repo: GraphRepository, src: str, label: str, tgt: str, source_id: str, eid: str) -> str:
    repo.create_edge(
        EdgeCreate(
            template_id="rel",
            source_node_id=src,
            target_node_id=tgt,
            label=label,
            source_id=source_id,
        ),
        custom_id=eid,
    )
    return eid


def _supersede(repo: GraphRepository, source_id: str) -> list[Superseded]:
    return supersede_memory_edges(repo, source_id, single_valued=MEMORY_SINGLE_VALUED)


def test_paris_then_lyon_closes_paris_with_lyons_date_and_keeps_both(repo: GraphRepository) -> None:
    paris = _fact(repo, "pierre", "located_in", "paris", "m_paris", "e_paris")
    assert _supersede(repo, "m_paris") == []
    lyon = _fact(repo, "pierre", "located_in", "lyon", "m_lyon", "e_lyon")
    closed = _supersede(repo, "m_lyon")
    assert closed == [
        Superseded(
            subject="Pierre",
            label="located_in",
            old_object="Paris",
            old_by="alice",
            old_from=date(2026, 3, 2),
            new_object="Lyon",
            new_by="alice",
            new_from=date(2026, 10, 20),
            self_closed=False,
        )
    ]
    old, new = repo.get_edge(paris), repo.get_edge(lyon)
    assert old is not None and new is not None
    assert (old.valid_from, old.valid_to, old.closed_by_source_id) == (
        date(2026, 3, 2),
        date(2026, 10, 20),
        "m_lyon",
    )
    assert (new.valid_from, new.valid_to, new.closed_by_source_id) == (
        date(2026, 10, 20),
        None,
        None,
    )


def test_knows_anna_then_boris_closes_nothing(repo: GraphRepository) -> None:
    _fact(repo, "pierre", "friend_of", "anna", "m_paris", "e1")
    _fact(repo, "pierre", "friend_of", "boris", "m_lyon", "e2")
    assert _supersede(repo, "m_lyon") == []
    assert all(e.valid_to is None for e in repo.list_edges())


def test_the_same_fact_twice_adds_a_member_not_a_closure(repo: GraphRepository) -> None:
    lyon = _fact(repo, "pierre", "located_in", "lyon", "m_paris", "e_lyon")
    repo.record_edge_sources([(lyon, "m_lyon", {"label": "located_in"})])
    assert _supersede(repo, "m_lyon") == []
    edge = repo.get_edge(lyon)
    assert edge is not None and edge.valid_to is None
    assert edge.valid_from == date(2026, 3, 2)  # the earliest member's date


def test_an_older_arrival_closes_itself(repo: GraphRepository) -> None:
    _fact(repo, "pierre", "located_in", "paris", "m_paris", "e_paris")
    _supersede(repo, "m_paris")
    old = _fact(repo, "pierre", "located_in", "lyon", "m_old", "e_old")
    closed = _supersede(repo, "m_old")
    assert len(closed) == 1 and closed[0].self_closed is True
    assert (closed[0].old_object, closed[0].new_object) == ("Lyon", "Paris")
    edge = repo.get_edge(old)
    assert edge is not None
    assert (edge.valid_from, edge.valid_to, edge.closed_by_source_id) == (
        date(2026, 1, 1),
        date(2026, 3, 2),
        "m_paris",
    )
    paris = repo.get_edge("e_paris")
    assert paris is not None and paris.valid_to is None


def test_equal_dates_the_later_created_wins_and_nothing_closes_twice(repo: GraphRepository) -> None:
    _fact(repo, "pierre", "located_in", "lyon", "m_lyon", "e_lyon")
    _supersede(repo, "m_lyon")
    _fact(repo, "pierre", "located_in", "paris", "m_same_day", "e_same")  # created later, same date
    closed = _supersede(repo, "m_same_day")
    assert [(c.old_object, c.new_object, c.self_closed) for c in closed] == [
        ("Lyon", "Paris", False)
    ]
    assert _supersede(repo, "m_same_day") == []  # a second run finds nothing open to close
    lyon = repo.get_edge("e_lyon")
    assert lyon is not None and lyon.closed_by_source_id == "m_same_day"


def test_a_timeless_document_fact_is_never_closed(repo: GraphRepository) -> None:
    _fact(repo, "pierre", "located_in", "paris", "doc", "e_doc")
    _fact(repo, "pierre", "located_in", "lyon", "m_lyon", "e_lyon")
    assert _supersede(repo, "m_lyon") == []
    doc = repo.get_edge("e_doc")
    assert doc is not None and doc.valid_from is None and doc.valid_to is None


def test_rebuild_from_members_sets_valid_from_to_the_earliest_member(repo: GraphRepository) -> None:
    lyon = _fact(repo, "pierre", "located_in", "lyon", "m_lyon", "e_lyon")
    repo.record_edge_sources([(lyon, "m_paris", {"label": "located_in"})])
    rebuild_from_members(repo, [], [lyon], DEFAULT_NAME_CONVENTIONS)
    edge = repo.get_edge(lyon)
    assert edge is not None and edge.valid_from == date(2026, 3, 2)
    repo.record_edge_sources([(lyon, "doc", {"label": "located_in"})])
    rebuild_from_members(repo, [], [lyon], DEFAULT_NAME_CONVENTIONS)
    edge = repo.get_edge(lyon)
    assert edge is not None and edge.valid_from == date(
        2026, 3, 2
    )  # a timeless member changes nothing


def test_a_rebuild_with_no_dated_member_leaves_the_edge_timeless(repo: GraphRepository) -> None:
    doc = _fact(repo, "pierre", "located_in", "paris", "doc", "e_doc")
    rebuild_from_members(repo, [], [doc], DEFAULT_NAME_CONVENTIONS)
    edge = repo.get_edge(doc)
    assert edge is not None and edge.valid_from is None


def test_a_fact_restated_after_its_replacement_is_current_again(repo: GraphRepository) -> None:
    """Paris, then Lyon, then Paris again: Paris is open, Lyon closed at the restatement's date."""
    paris = _fact(repo, "pierre", "located_in", "paris", "m_old", "e_paris")  # 2026-01-01
    _supersede(repo, "m_old")
    lyon = _fact(repo, "pierre", "located_in", "lyon", "m_paris", "e_lyon")  # 2026-03-02
    _supersede(repo, "m_paris")
    repo.record_edge_sources([(paris, "m_lyon", {"label": "located_in"})])  # restated 2026-10-20
    closed = _supersede(repo, "m_lyon")
    assert [(c.old_object, c.new_object, c.new_from) for c in closed] == [
        ("Lyon", "Paris", date(2026, 10, 20))
    ]
    paris_edge, lyon_edge = repo.get_edge(paris), repo.get_edge(lyon)
    assert paris_edge is not None and lyon_edge is not None
    assert (paris_edge.valid_to, paris_edge.closed_by_source_id) == (None, None)
    assert (lyon_edge.valid_to, lyon_edge.closed_by_source_id) == (date(2026, 10, 20), "m_lyon")


def test_recompute_after_a_release_closes_what_the_reopened_fact_contradicts(
    repo: GraphRepository,
) -> None:
    """Moscow, Paris, Lyon; releasing Paris leaves Moscow closed by Lyon, not reopened for good."""
    from chaoscypher_core.services.graph.memory.supersession import recompute_single_valued

    _fact(repo, "pierre", "located_in", "paris", "m_old", "e_moscow")  # 2026-01-01, "Moscow" role
    _supersede(repo, "m_old")
    _fact(repo, "pierre", "located_in", "lyon", "m_paris", "e_middle")  # 2026-03-02
    _supersede(repo, "m_paris")
    _fact(repo, "pierre", "located_in", "anna", "m_lyon", "e_last")  # 2026-10-20
    _supersede(repo, "m_lyon")
    repo.release_source("m_paris")  # the middle memory goes; its closure of the first reopens
    recompute_single_valued(repo, "pierre", "located_in")
    first, last = repo.get_edge("e_moscow"), repo.get_edge("e_last")
    assert first is not None and last is not None
    assert (first.valid_to, first.closed_by_source_id) == (date(2026, 10, 20), "m_lyon")
    assert last.valid_to is None
