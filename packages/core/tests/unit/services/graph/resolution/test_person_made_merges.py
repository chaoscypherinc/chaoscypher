# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The identity pass and a person-made node: it is never dropped, and nothing folds into it.

A node a person made (``person_made``, migration 0025) that the resolver finds to be one
entity with a source's node keeps its row: the source's node is attached to it (its
memberships, citations and edges move) and no text of the source's folds into the
person's. A later release of that source takes its membership and its own edges, and
the person's node reads as it did before the merge.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import GraphEdge, SourceRow
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.models import EdgeCreate, NodeCreate, TemplateCreate
from chaoscypher_core.services.graph.resolution import apply, resolve_graph
from chaoscypher_core.services.graph.resolution.apply import apply_resolution, attach_node
from chaoscypher_core.services.graph.resolution.plan import AliasDrop, Merge, ResolutionPlan
from tests.fakes.domain_tables import domain_conventions


LITERARY = domain_conventions("literary")
DB = "test"


@pytest.fixture
def repo(tmp_path: Path) -> Generator[GraphRepository]:
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name=DB)
    adapter.connect()
    assert adapter.session is not None
    for source_id in ("doc", "other"):
        adapter.session.add(
            SourceRow(
                id=source_id,
                database_name=DB,
                filename=f"{source_id}.txt",
                filepath=f"/tmp/{source_id}.txt",
                file_type="text",
                file_size=1,
                content_hash=f"hash-{source_id}",
                status="committed",
            )
        )
    adapter.session.commit()
    repo = GraphRepository(adapter.session, database_name=DB)
    for tpl, kind in (("character", "node"), ("rel", "edge")):
        repo.create_template(TemplateCreate(name=tpl, template_type=kind), custom_id=tpl)
    yield repo
    adapter.disconnect()


def _node(repo: GraphRepository, nid: str, *, person: bool, **props: Any) -> str:
    repo.create_node(
        NodeCreate(
            template_id="character",
            label="Pierre Bezukhov",
            entity_type="Person",
            properties=props,
            source_id=None if person else "doc",
        ),
        custom_id=nid,
        person_made=person,
    )
    return nid


def _reading(repo: GraphRepository, node_id: str) -> tuple[Any, ...]:
    node = repo.get_node(node_id, include_embedding=False)
    assert node is not None
    return (node.label, node.entity_type, node.source_id, node.properties)


def _documents_pierre(repo: GraphRepository) -> str:
    """The document's Pierre, better connected than the person's (the resolver would keep it)."""
    docs = _node(repo, "a_docs", person=False, description="A count.", aliases=["Pierre"])
    for i in range(3):
        other = repo.create_node(
            NodeCreate(
                template_id="character",
                label=f"Friend {i}",
                entity_type="Person",
                source_id="doc",
            )
        )
        repo.create_edge(
            EdgeCreate(
                template_id="rel",
                source_node_id=docs,
                target_node_id=other.id,
                label="friend_of",
                source_id="doc",
            )
        )
    return docs


def test_a_person_made_node_is_kept_and_nothing_folds_into_it(repo: GraphRepository) -> None:
    person = _node(repo, "z_person", person=True, kept_by="the desk")
    before = _reading(repo, person)
    docs = _documents_pierre(repo)

    resolve_graph(repo, conventions=LITERARY)

    assert repo.get_node(docs, include_embedding=False) is None
    assert _reading(repo, person) == before
    assert "doc" in repo.node_members(person)
    assert len(repo.list_edges(source_node_id=person)) == 3

    repo.release_source("doc")

    assert _reading(repo, person) == before
    assert repo.list_edges(source_node_id=person) == []


def test_two_person_made_nodes_are_never_merged(repo: GraphRepository) -> None:
    first = _node(repo, "p1", person=True)
    second = _node(repo, "p2", person=True)

    resolve_graph(repo, conventions=LITERARY)

    assert repo.get_node(first, include_embedding=False) is not None
    assert repo.get_node(second, include_embedding=False) is not None


def test_an_alias_drop_never_rewrites_a_person_made_node(repo: GraphRepository) -> None:
    person = _node(repo, "p1", person=True, aliases=["Pierre"])
    other = _node(repo, "d1", person=False)
    before = _reading(repo, person)

    apply_resolution(
        repo, ResolutionPlan(alias_drops=[AliasDrop(node=person, alias="Pierre", owner=other)])
    )

    assert _reading(repo, person) == before


def _edge(repo: GraphRepository, src: str, tgt: str, *, person: bool) -> str:
    edge = repo.create_edge(
        EdgeCreate(
            template_id="rel",
            source_node_id=src,
            target_node_id=tgt,
            label="located_in",
            source_id=None if person else "doc",
        ),
        person_made=person,
    )
    return edge.id


def _place(repo: GraphRepository, label: str, *sources: str) -> str:
    """A node the given sources attest (the first homes it)."""
    node = repo.create_node(
        NodeCreate(
            template_id="character", label=label, entity_type="Location", source_id=sources[0]
        )
    )
    repo.record_node_sources([(node.id, s, {"label": label}) for s in sources[1:]])
    return node.id


def _window(repo: GraphRepository, edge_id: str) -> tuple[Any, ...]:
    edge = repo.get_edge(edge_id)
    assert edge is not None
    return (edge.valid_from, edge.valid_to, edge.closed_by_source_id)


def test_a_persons_undated_edge_stays_undated_after_an_attach_and_the_release(
    repo: GraphRepository,
) -> None:
    """A person's undated edge holds always: no source's window is written onto it."""
    person = _node(repo, "p1", person=True)
    twin = _node(repo, "d1", person=False)
    paris = _place(repo, "Paris", "doc", "other")
    undated = _edge(repo, person, paris, person=True)
    moved = _edge(repo, twin, paris, person=False)
    repo.set_edge_validity(
        moved, valid_from=date(2026, 3, 2), valid_to=date(2026, 10, 20), closed_by_source_id="doc"
    )

    attach_node(repo, person, twin)
    assert _window(repo, undated) == (None, None, None)
    repo.release_source("doc")

    assert _window(repo, undated) == (None, None, None)
    edge = repo.get_edge(undated)
    assert edge is not None and edge.properties == {}


def test_an_attach_moves_every_edge_past_one_page(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(apply, "PAGE", 2)
    person = _node(repo, "p1", person=True)
    twin = _node(repo, "d1", person=False)
    for i in range(5):
        friend = repo.create_node(
            NodeCreate(template_id="character", label=f"Friend {i}", source_id="doc")
        )
        _edge(repo, twin, friend.id, person=False)

    attach_node(repo, person, twin)

    assert len(repo.list_edges(source_node_id=person, limit=100)) == 5


# --- edges in a merge: a person's edge is never absorbed, re-created or rewritten ----------


def _merge(repo: GraphRepository, keep: str, drop: str) -> None:
    """A planned merge of two source nodes (fold mode: the resolver's own)."""
    apply_resolution(repo, ResolutionPlan(merges=[Merge(keep=keep, drop=drop, why="test")]))


def _doc_node(repo: GraphRepository, nid: str, label: str) -> str:
    repo.create_node(
        NodeCreate(template_id="character", label=label, entity_type="Person", source_id="doc"),
        custom_id=nid,
    )
    repo.record_node_sources([(nid, "other", {"label": label})])  # outlives the doc
    return nid


def _knows(repo: GraphRepository, src: str, tgt: str, *, person: bool, **props: Any) -> str:
    edge = repo.create_edge(
        EdgeCreate(
            template_id="rel",
            source_node_id=src,
            target_node_id=tgt,
            label="knows",
            properties=props,
            source_id=None if person else "doc",
        ),
        person_made=person,
    )
    return edge.id


def _stamp_iri(repo: GraphRepository, edge_id: str) -> None:
    row = repo.session.get(GraphEdge, edge_id)
    assert row is not None
    row.ccx_iri = f"urn:cc:edge:{edge_id}"
    repo.session.add(row)
    repo.session.commit()


def test_a_persons_edge_survives_a_merge_and_the_documents_release(
    repo: GraphRepository,
) -> None:
    """The review's repro: the document's Pierre Bezukhov knows Natasha, a person wrote
    Pierre knows Natasha; the merge folds Pierre in, the document goes, the fact stays.
    """
    full = _doc_node(repo, "full", "Pierre Bezukhov")
    short = _doc_node(repo, "short", "Pierre")
    natasha = _doc_node(repo, "natasha", "Natasha")
    documents = _knows(repo, full, natasha, person=False, justification="They meet.")
    persons = _knows(repo, short, natasha, person=True, note="a person's")
    before = repo.get_edge(persons)

    _merge(repo, full, short)
    repo.release_source("doc")

    after = repo.get_edge(persons)
    assert after is not None and before is not None
    assert (after.source_node_id, after.target_node_id) == (full, natasha)
    assert after.properties == {"note": "a person's"}
    assert repo.get_edge(documents) is None  # the duplicate went into the person's edge


def test_a_document_edge_into_a_persons_kept_edge_brings_memberships_only(
    repo: GraphRepository,
) -> None:
    full = _doc_node(repo, "full", "Pierre Bezukhov")
    short = _doc_node(repo, "short", "Pierre")
    natasha = _doc_node(repo, "natasha", "Natasha")
    persons = _knows(repo, full, natasha, person=True, note="a person's")
    documents = _knows(repo, short, natasha, person=False, justification="They meet.")
    repo.set_edge_validity(  # closed: a fold would hand the kept edge this window
        documents,
        valid_from=date(2026, 3, 2),
        valid_to=date(2026, 10, 20),
        closed_by_source_id="doc",
    )

    _merge(repo, full, short)

    edge = repo.get_edge(persons)
    assert edge is not None
    assert edge.properties == {"note": "a person's"}
    assert _window(repo, persons) == (None, None, None)
    assert "doc" in repo.edge_members(persons)
    assert repo.get_edge(documents) is None


def test_two_persons_edges_of_one_fact_are_both_kept(repo: GraphRepository) -> None:
    full = _doc_node(repo, "full", "Pierre Bezukhov")
    short = _doc_node(repo, "short", "Pierre")
    natasha = _doc_node(repo, "natasha", "Natasha")
    first = _knows(repo, full, natasha, person=True)
    second = _knows(repo, short, natasha, person=True)

    _merge(repo, full, short)

    kept = [repo.get_edge(e) for e in (first, second)]
    assert all(e is not None and e.source_node_id == full for e in kept)


def test_a_moved_persons_edge_keeps_its_id_created_at_and_iri(repo: GraphRepository) -> None:
    full = _doc_node(repo, "full", "Pierre Bezukhov")
    short = _doc_node(repo, "short", "Pierre")
    natasha = _doc_node(repo, "natasha", "Natasha")
    persons = _knows(repo, short, natasha, person=True)
    _stamp_iri(repo, persons)
    row = repo.session.get(GraphEdge, persons)
    assert row is not None
    created, iri = row.created_at, row.ccx_iri

    _merge(repo, full, short)

    repo.session.expire_all()
    moved = repo.session.get(GraphEdge, persons)
    assert moved is not None
    assert (moved.source_node_id, moved.created_at, moved.ccx_iri) == (full, created, iri)


def test_an_edge_only_a_disabled_source_attests_is_moved_not_lost(
    repo: GraphRepository,
) -> None:
    full = _doc_node(repo, "full", "Pierre Bezukhov")
    short = _doc_node(repo, "short", "Pierre")
    natasha = _doc_node(repo, "natasha", "Natasha")
    hidden = repo.create_edge(
        EdgeCreate(
            template_id="rel",
            source_node_id=short,
            target_node_id=natasha,
            label="knows",
            source_id="other",
        )
    ).id
    _knows(repo, full, natasha, person=False)  # the same fact, already on the kept node
    row = repo.session.get(SourceRow, "other")
    assert row is not None
    row.enabled = False
    repo.session.add(row)
    repo.session.commit()

    _merge(repo, full, short)

    edges = repo.list_edges(source_node_id=full, include_disabled_sources=True, limit=100)
    assert len(edges) == 1  # folded into the kept node's copy: no duplicate
    assert "other" in repo.edge_members(edges[0].id)
    assert repo.get_edge(hidden) is None or hidden == edges[0].id


def test_repointing_an_edge_that_is_not_there_raises(repo: GraphRepository) -> None:
    with pytest.raises(NotFoundError):
        repo.repoint_edge("edge_missing", source_node_id="a", target_node_id="b")


def test_a_persons_edge_a_merge_turns_into_a_loop_is_kept_and_listed(
    repo: GraphRepository,
) -> None:
    """A person linked the two nodes the merge folds together: the edge becomes a loop,
    and stays (a person's row is never deleted by a merge), counted as moved once.
    """
    full = _doc_node(repo, "full", "Pierre Bezukhov")
    short = _doc_node(repo, "short", "Pierre")
    loop = _knows(repo, full, short, person=True)
    own = _knows(repo, short, short, person=True)  # a loop already: read from both ends

    report = apply_resolution(
        repo, ResolutionPlan(merges=[Merge(keep=full, drop=short, why="test")])
    )

    edge = repo.get_edge(loop)
    assert edge is not None and (edge.source_node_id, edge.target_node_id) == (full, full)
    assert {loop, own} <= {e.id for e in repo.list_edges(source_node_id=full, limit=100)}
    assert report.edges_moved == 2  # each edge once


def test_a_source_copy_read_first_is_absorbed_into_the_persons_edge(
    repo: GraphRepository,
) -> None:
    """Both copies of one fact sit on the dropped node, the source's read first: the
    person's edge still keeps its row, and the source's folds into it.
    """
    full = _doc_node(repo, "full", "Pierre Bezukhov")
    short = _doc_node(repo, "short", "Pierre")
    natasha = _doc_node(repo, "natasha", "Natasha")
    documents = _knows(repo, short, natasha, person=False)  # older row: listed first
    persons = _knows(repo, short, natasha, person=True, note="a person's")

    _merge(repo, full, short)

    edges = repo.list_edges(source_node_id=full, limit=100)
    assert [e.id for e in edges] == [persons]
    assert edges[0].properties == {"note": "a person's"}
    assert "doc" in repo.edge_members(persons)
    assert repo.get_edge(documents) is None
