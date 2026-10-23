# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Which sources attest a graph node or edge (#795, spec 2026-10-17 D1/D2).

``source_id`` stays the home source; ``graph_node_sources`` / ``graph_edge_sources``
hold one membership per attesting source with the snapshot it wrote. Pins that
every write path records it, that the CCX stamp adds a member without moving the
home, and that migration 0019 backfills one membership per existing row, in the
same shape ``node_record`` / ``edge_record`` give.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlmodel import Session, SQLModel, select

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.models import (
    GraphEdge,
    GraphNode,
    GraphNodeSource,
    GraphTemplate,
    SourceRow,
)
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_membership_ops import (
    ABSORBED_KEY,
    edge_snapshot,
    node_snapshot,
)
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
from chaoscypher_core.database.migrations.runner import downgrade_to, upgrade_to, upgrade_to_head
from chaoscypher_core.models import (
    EdgeCreate,
    NodeCreate,
    NodeUpdate,
    ReleasedRows,
    TemplateCreate,
)
from chaoscypher_core.services.graph.resolution.apply import (
    edge_record,
    node_create_snapshot,
    node_record,
)


if TYPE_CHECKING:
    from pathlib import Path

DB = "test"


def _source(sid: str) -> SourceRow:
    return SourceRow(
        id=sid,
        database_name=DB,
        filename=f"{sid}.txt",
        filepath=f"/tmp/{sid}.txt",
        file_type="text",
        file_size=1,
        content_hash=f"hash-{sid}",
        status="committed",
    )


@pytest.fixture
def repo(tmp_path: Path) -> GraphRepository:
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(db_path), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name=DB)
    adapter.connect()
    session = adapter.session
    assert session is not None
    for sid in ("a", "b"):
        session.add(_source(sid))
    session.commit()
    graph = GraphRepository(session, database_name=DB)
    graph.create_template(TemplateCreate(name="thing", template_type="node"), custom_id="t_node")
    graph.create_template(TemplateCreate(name="rel", template_type="edge"), custom_id="t_edge")
    return graph


def _node(label: str, source_id: str | None, **properties: object) -> NodeCreate:
    return NodeCreate(
        template_id="t_node",
        label=label,
        entity_type="character",
        properties=dict(properties),
        embedding=[0.1, 0.2],
        source_id=source_id,
    )


async def test_upsert_records_one_membership_with_its_snapshot(repo: GraphRepository) -> None:
    (n1, n2), _ = await repo.upsert_nodes_batch(
        [_node("Pierre", "a", aliases=["Pierre B."]), _node("Natasha", "a")]
    )
    members = repo.node_members(n1.id)
    assert members == {"a": node_record(n1)}
    assert "embedding" not in members["a"]
    assert members["a"]["aliases"] == ["Pierre B."]
    assert set(repo.node_members(n2.id)) == {"a"}

    (edge,), _ = await repo.upsert_edges_batch(
        [
            EdgeCreate(
                template_id="t_edge",
                source_node_id=n1.id,
                target_node_id=n2.id,
                label="loves",
                properties={"since": "1805"},
                source_id="a",
            )
        ]
    )
    assert repo.edge_members(edge.id) == {"a": edge_record(edge)}


async def test_rerunning_an_upsert_keeps_the_one_membership(repo: GraphRepository) -> None:
    creates = [_node("Pierre", "a")]
    (node,), _ = await repo.upsert_nodes_batch(creates)
    await repo.upsert_nodes_batch(creates)
    assert set(repo.node_members(node.id)) == {"a"}


async def test_a_reused_row_the_source_no_longer_attests_is_attested_again(
    repo: GraphRepository,
) -> None:
    """Source b's node outlived b's release under a; b's next upsert lands on it, attests it.

    Records b's membership with the reading b writes now (not a copy of the row), reports
    the id for the rebuild, and leaves a's membership and the row itself alone.
    """
    (b_node,), _ = await repo.upsert_nodes_batch([_node("Pierre Bezukhov", "b", note="b's")])
    (a_node,), _ = await repo.upsert_nodes_batch([_node("Pierre", "a")])
    repo.absorb_node(b_node.id, a_node.id)
    repo.delete_node(a_node.id)
    repo.release_source("b")
    repo.session.commit()
    assert set(repo.node_members(b_node.id)) == {"a"}

    reattested: set[str] = set()
    again = _node("Pierre Bezukhov", "b", note="b's again")
    (node,), inserted = await repo.upsert_nodes_batch([again], reattested=reattested)
    repo.session.commit()
    assert (node.id, inserted) == (b_node.id, 0)
    assert reattested == {b_node.id}
    members = repo.node_members(b_node.id)
    assert set(members) == {"a", "b"}
    assert members["b"]["label"] == "Pierre Bezukhov"
    assert members["b"]["properties"]["note"] == "b's again"

    # the same upsert again is b's own resume: first write wins, nothing to rebuild
    resumed: set[str] = set()
    await repo.upsert_nodes_batch([_node("Pierre Bezukhov", "b", note="third")], reattested=resumed)
    assert resumed == set()
    assert repo.node_members(b_node.id)["b"]["properties"]["note"] == "b's again"


async def test_a_reused_edge_the_source_no_longer_attests_is_attested_again(
    repo: GraphRepository,
) -> None:
    (n1, n2), _ = await repo.upsert_nodes_batch([_node("X", "a"), _node("Y", "a")])
    create = _edge(n1.id, n2.id, "b")
    (edge,), _ = await repo.upsert_edges_batch([create])
    repo.record_edge_sources([(edge.id, "a", {"label": "knows", "properties": {}})])
    repo.release_source("b")
    repo.session.commit()
    assert set(repo.edge_members(edge.id)) == {"a"}
    reattested: set[str] = set()
    (again,), inserted = await repo.upsert_edges_batch([create], reattested=reattested)
    assert (again.id, inserted, reattested) == (edge.id, 0, {edge.id})
    assert set(repo.edge_members(edge.id)) == {"a", "b"}


async def test_a_reused_template_goes_back_to_the_source_its_id_names(
    repo: GraphRepository,
) -> None:
    """The template variant: a's release handed b's template to a; b's commit takes it back."""
    create = TemplateCreate(name="person", template_type="node", source_id="b")
    (template,), _ = await repo.upsert_templates_batch([create])
    row = repo.session.get(GraphTemplate, template.id)
    assert row is not None
    row.source_id = "a"  # as a release re-homes a kept template (D6)
    repo.session.add(row)
    repo.session.commit()

    (again,), inserted = await repo.upsert_templates_batch([create])
    repo.session.expire_all()
    assert (again.id, inserted) == (template.id, 0)
    assert repo.session.get(GraphTemplate, template.id).source_id == "b"  # type: ignore[union-attr]

    row = repo.session.get(GraphTemplate, template.id)
    row.source_id = None  # type: ignore[union-attr]
    repo.session.commit()
    single, created = repo.upsert_template(create)
    repo.session.expire_all()
    assert (single.id, created) == (template.id, False)
    assert repo.session.get(GraphTemplate, template.id).source_id == "b"  # type: ignore[union-attr]


async def test_every_create_path_with_a_source_records_a_membership(repo: GraphRepository) -> None:
    single = repo.create_node(_node("One", "a"))
    (batched,) = await repo.create_nodes_batch([_node("Two", "b")])
    ccx = repo.upsert_node_by_ccx_iri("iri:3", _node("Three", None), DB, source_id="a")
    assert set(repo.node_members(single.id)) == {"a"}
    assert set(repo.node_members(batched.id)) == {"b"}
    assert set(repo.node_members(ccx["id"])) == {"a"}

    make = lambda label: EdgeCreate(  # noqa: E731
        template_id="t_edge",
        source_node_id=single.id,
        target_node_id=batched.id,
        label=label,
        source_id="a",
    )
    edge = repo.create_edge(make("e1"))
    (batch_edge,) = await repo.create_edges_batch([make("e2")])
    ccx_edge = repo.upsert_edge_by_ccx_iri("iri:e", make("e3"), DB)
    for edge_id in (edge.id, batch_edge.id, ccx_edge["id"]):
        assert set(repo.edge_members(edge_id)) == {"a"}


def test_a_node_without_a_source_has_no_membership(repo: GraphRepository) -> None:
    node = repo.create_node(_node("Orphan", None))
    assert repo.node_members(node.id) == {}


def test_ccx_link_records_every_linking_source(repo: GraphRepository) -> None:
    stray = repo.create_node(_node("Stray", None))
    homed = repo.create_node(_node("Homed", "a"))
    assert repo.assign_source_to_nodes([stray.id, homed.id], "b", DB) == 1  # one home stamped

    assert set(repo.node_members(stray.id)) == {"b"}
    assert set(repo.node_members(homed.id)) == {"a", "b"}
    home, stamped = repo.get_node(homed.id), repo.get_node(stray.id)
    assert home is not None
    assert stamped is not None
    assert home.source_id == "a"  # the home does not move
    assert stamped.source_id == "b"
    assert repo.node_members(stray.id)["b"]["properties"]["source_document_id"] == "b"


def test_a_membership_goes_with_its_source(repo: GraphRepository) -> None:
    node = repo.create_node(_node("Pierre", "a"))
    repo.session.delete(repo.session.get(SourceRow, "a"))
    repo.session.commit()
    assert repo.node_members(node.id) == {}


def test_recording_more_rows_than_one_statement_can_bind(repo: GraphRepository) -> None:
    """A whole-source commit records thousands of memberships in one call."""
    count = 12_000  # 3 parameters each: past SQLite's 32766-variable limit in one INSERT
    for i in range(count):  # the owner rows must exist (FK)
        repo.session.add(
            GraphNode(
                id=f"n{i}",
                database_name=DB,
                graph_name="knowledge",
                template_id="t_node",
                label=f"N{i}",
            )
        )
    repo.record_node_sources([(f"n{i}", "b", {"i": i}) for i in range(count)])
    repo.session.commit()
    assert repo.node_members("n11999") == {"b": {"i": 11999}}
    assert repo.node_members("n0") == {"b": {"i": 0}}


def test_a_rewrite_by_the_same_source_refreshes_its_snapshot(repo: GraphRepository) -> None:
    node = repo.create_node(_node("Pierre", "a", description="old"))
    repo.record_node_sources([(node.id, "a", {"label": "newer"})])
    assert repo.node_members(node.id) == {"a": {"label": "newer"}}


def test_ccx_upsert_existing_row_refreshes_snapshot_and_keeps_old_home(
    repo: GraphRepository,
) -> None:
    first = repo.upsert_node_by_ccx_iri("iri:n", _node("Old name", None, v=1), DB, source_id="a")
    repo.upsert_node_by_ccx_iri("iri:n", _node("New name", None, v=2), DB, source_id="a")
    assert repo.node_members(first["id"])["a"]["label"] == "New name"
    assert repo.node_members(first["id"])["a"]["properties"]["v"] == 2

    repo.upsert_node_by_ccx_iri("iri:n", _node("Moved", None, v=3), DB, source_id="b")
    members = repo.node_members(first["id"])
    assert members["a"]["label"] == "New name"  # a still linked it
    assert members["b"]["label"] == "Moved"

    n1 = repo.create_node(_node("S", "a"))
    n2 = repo.create_node(_node("T", "a"))

    def edge(label: str) -> EdgeCreate:
        return EdgeCreate(
            template_id="t_edge",
            source_node_id=n1.id,
            target_node_id=n2.id,
            label=label,
            properties={"label_v": label},
        )

    created = repo.upsert_edge_by_ccx_iri("iri:e", edge("first"), DB, source_id="a")
    repo.upsert_edge_by_ccx_iri("iri:e", edge("second"), DB, source_id="a")
    assert repo.edge_members(created["id"])["a"]["label"] == "second"
    repo.upsert_edge_by_ccx_iri("iri:e", edge("third"), DB, source_id="b")
    edge_members = repo.edge_members(created["id"])
    assert edge_members["a"]["label"] == "second"
    assert edge_members["b"]["label"] == "third"


def test_absorb_moves_a_members_row_and_folds_a_shared_source(repo: GraphRepository) -> None:
    """D2: the dropped node's other sources move over; a source both hold keeps both readings."""
    keep = repo.create_node(_node("Pierre", "a"))
    drop = repo.create_node(_node("Pierre Bezúkhov", "b"))
    repo.record_node_sources([(drop.id, "a", {"label": "P.", ABSORBED_KEY: [{"label": "Pétya"}]})])
    kept_a = repo.node_members(keep.id)["a"]
    dropped_b = repo.node_members(drop.id)["b"]

    repo.absorb_node(keep.id, drop.id)

    members = repo.node_members(keep.id)
    assert set(members) == {"a", "b"}
    assert members["b"] == dropped_b  # moved unchanged
    assert {k: v for k, v in members["a"].items() if k != ABSORBED_KEY} == kept_a
    assert members["a"][ABSORBED_KEY] == [{"label": "P."}, {"label": "Pétya"}]  # flat
    assert repo.node_members(drop.id) == {}


def test_members_read_after_an_absorb_even_with_a_row_held(repo: GraphRepository) -> None:
    """``_absorb`` writes past the identity map; a loaded row must not be what readers see."""
    keep = repo.create_node(_node("Pierre", "a"))
    drop = repo.create_node(_node("P.", "a"))
    held = repo.session.get(GraphNodeSource, (keep.id, "a"))
    assert held is not None
    repo.absorb_node(keep.id, drop.id)
    assert repo.node_members(keep.id)["a"][ABSORBED_KEY][0]["label"] == "P."


def test_absorb_edge_moves_memberships(repo: GraphRepository) -> None:
    n1 = repo.create_node(_node("S", "a"))
    n2 = repo.create_node(_node("T", "a"))

    def make(sid: str) -> EdgeCreate:
        return EdgeCreate(
            template_id="t_edge",
            source_node_id=n1.id,
            target_node_id=n2.id,
            label="knows",
            source_id=sid,
        )

    keep, drop, twin = (
        repo.create_edge(make("a")),
        repo.create_edge(make("b")),
        repo.create_edge(make("a")),
    )
    # a second reading by the same source: it says something the kept one does not
    repo.record_edge_sources(
        [(twin.id, "a", {**edge_snapshot(_edge_row(repo, twin.id)), "label": "met"})]
    )
    repo.absorb_edge(keep.id, drop.id)
    repo.absorb_edge(keep.id, twin.id)
    members = repo.edge_members(keep.id)
    assert set(members) == {"a", "b"}
    assert [s["id"] for s in members["a"][ABSORBED_KEY]] == [twin.id]
    assert repo.edge_members(drop.id) == {} == repo.edge_members(twin.id)


def _edge_row(repo: GraphRepository, edge_id: str) -> GraphEdge:
    row = repo.session.get(GraphEdge, edge_id)
    assert row is not None
    return row


def test_absorbing_the_edge_a_moved_copy_came_from_adds_no_reading(repo: GraphRepository) -> None:
    """A moved edge is the same reading as its original: no ``absorbed`` duplicate (Task 2 review).

    ``_move_edges`` creates the copy with the original's home source (which records a
    membership for it) and then absorbs the original: the two snapshots differ only in
    the id, the endpoints and ``resolved_from``, so nothing is appended.
    """
    n1, n2, n3 = (repo.create_node(_node(x, "a")) for x in ("S", "T", "U"))
    original = repo.create_edge(
        EdgeCreate(
            template_id="t_edge",
            source_node_id=n1.id,
            target_node_id=n2.id,
            label="knows",
            properties={"justification": "they met"},
            source_id="a",
        )
    )
    moved = repo.create_edge(
        EdgeCreate(
            template_id="t_edge",
            source_node_id=n3.id,
            target_node_id=n2.id,
            label="knows",
            properties={"justification": "they met", "resolved_from": n1.id},
            source_id="a",
        )
    )
    kept_before = repo.edge_members(moved.id)["a"]
    repo.absorb_edge(moved.id, original.id)
    assert repo.edge_members(moved.id) == {"a": kept_before}


def test_absorbing_an_identical_reading_still_carries_what_it_absorbed(
    repo: GraphRepository,
) -> None:
    keep = repo.create_node(_node("Pierre", "a"))
    drop = repo.create_node(_node("Pierre", "a"))
    repo.record_node_sources(
        [(drop.id, "a", {**repo.node_members(drop.id)["a"], ABSORBED_KEY: [{"label": "P."}]})]
    )
    repo.absorb_node(keep.id, drop.id)
    assert repo.node_members(keep.id)["a"][ABSORBED_KEY] == [{"label": "P."}]

    again = repo.create_node(_node("Pierre", "a"))  # a third copy of one reading, and P. again
    repo.record_node_sources(
        [(again.id, "a", {**repo.node_members(again.id)["a"], ABSORBED_KEY: [{"label": "P."}]})]
    )
    repo.absorb_node(keep.id, again.id)
    assert repo.node_members(keep.id)["a"][ABSORBED_KEY] == [{"label": "P."}]


def test_a_refresh_keeps_what_the_source_absorbed(repo: GraphRepository) -> None:
    """A source rewriting a merged node replaces its own reading, never the readings it folded in."""
    keep = repo.create_node(_node("Pierre", "a"))
    drop = repo.create_node(_node("P.", "a"))
    repo.absorb_node(keep.id, drop.id)
    repo.record_node_sources([(keep.id, "a", {"label": "Pierre Bezúkhov"})])
    members = repo.node_members(keep.id)
    assert members["a"]["label"] == "Pierre Bezúkhov"
    assert [s["label"] for s in members["a"][ABSORBED_KEY]] == ["P."]
    repo.record_node_sources([(drop.id, "b", {"label": "new"})])  # a fresh row: nothing to keep
    assert ABSORBED_KEY not in repo.node_members(keep.id).get("b", {})


# ---------------------------------------------------------------------------
# Release: what a source delete, re-commit, re-extract or reset takes away
# ---------------------------------------------------------------------------


def _edge(src: str, tgt: str, source_id: str | None, label: str = "knows") -> EdgeCreate:
    return EdgeCreate(
        template_id="t_edge",
        source_node_id=src,
        target_node_id=tgt,
        label=label,
        source_id=source_id,
    )


def _ids(repo: GraphRepository, model: type[GraphNode] | type[GraphEdge]) -> set[str]:
    repo.session.expire_all()
    return set(repo.session.exec(select(model.id)).all())


def test_release_keeps_rows_another_source_attests(repo: GraphRepository) -> None:
    """The no-merge CCX shape: homed at a ("first link wins"), linked by b too (#795 probe)."""
    shared = repo.create_node(_node("Shared Entity", "a"))
    other = repo.create_node(_node("Other", "b"))
    repo.assign_source_to_nodes([shared.id], "b", DB)
    edge = repo.create_edge(_edge(shared.id, other.id, "a"))
    repo.record_edge_sources([(edge.id, "b", edge_snapshot(_edge_row(repo, edge.id)))])

    released = repo.release_source("a")

    assert released == ReleasedRows(
        deleted_node_ids=frozenset(),
        deleted_edge_ids=frozenset(),
        touched_node_ids=frozenset({shared.id}),
        touched_edge_ids=frozenset({edge.id}),
    )
    assert {shared.id, other.id} <= _ids(repo, GraphNode)
    assert edge.id in _ids(repo, GraphEdge)
    assert set(repo.node_members(shared.id)) == {"b"}
    assert set(repo.edge_members(edge.id)) == {"b"}


def test_release_deletes_rows_only_the_source_attests(repo: GraphRepository) -> None:
    pierre = repo.create_node(_node("Pierre", "a"))
    natasha = repo.create_node(_node("Natasha", "a"))
    dolokhov = repo.create_node(_node("Dolokhov", "b"))
    mine = repo.create_node(_node("Hand-made", None))  # no source: never released
    loves = repo.create_edge(_edge(pierre.id, natasha.id, "a", "loves"))
    duels = repo.create_edge(_edge(pierre.id, dolokhov.id, "b", "duels"))  # b's, on a's node
    stays = repo.create_edge(_edge(dolokhov.id, mine.id, "b"))
    # a row homed at a with no membership at all (written before memberships existed)
    repo.session.add(
        GraphNode(
            id="legacy",
            database_name=DB,
            graph_name="knowledge",
            template_id="t_node",
            label="Legacy",
            source_id="a",
        )
    )
    repo.session.flush()

    released = repo.release_source("a")

    assert released.deleted_node_ids == {pierre.id, natasha.id, "legacy"}
    assert released.deleted_edge_ids == {loves.id, duels.id}  # duels went with its endpoint
    assert released.touched_node_ids == frozenset()
    assert released.touched_edge_ids == frozenset()
    assert _ids(repo, GraphNode) == {dolokhov.id, mine.id}
    assert _ids(repo, GraphEdge) == {stays.id}


def test_release_keeps_a_template_a_surviving_edge_uses(repo: GraphRepository) -> None:
    """Review Focus 4: a moved edge keeps a's template; the delete raises no RESTRICT error (D6)."""
    a_rel = repo.create_template(
        TemplateCreate(name="loves", template_type="edge", source_id="a"), custom_id="a_rel"
    )
    a_unused = repo.create_template(
        TemplateCreate(name="hates", template_type="edge", source_id="a"), custom_id="a_unused"
    )
    a_node = repo.create_template(
        TemplateCreate(name="person", template_type="node", source_id="a"), custom_id="a_node"
    )
    n1, n2 = repo.create_node(_node("S", "b")), repo.create_node(_node("T", "b"))
    moved = repo.create_edge(
        EdgeCreate(
            template_id=a_rel.id,
            source_node_id=n1.id,
            target_node_id=n2.id,
            label="loves",
            source_id="b",
        )
    )

    repo.release_source("a")
    repo.session.delete(repo.session.get(SourceRow, "a"))
    repo.session.commit()

    repo.session.expire_all()
    kept = repo.session.get(GraphTemplate, a_rel.id)
    assert kept is not None
    assert kept.source_id == "b"  # re-homed to a member of its surviving user (D6, revised)
    assert repo.session.get(GraphTemplate, a_unused.id) is None
    assert repo.session.get(GraphTemplate, a_node.id) is None
    edge = repo.session.get(GraphEdge, moved.id)
    assert edge is not None
    assert edge.template_id == a_rel.id


def _template_ids(repo: GraphRepository) -> set[str]:
    repo.session.expire_all()
    return set(repo.session.exec(select(GraphTemplate.id)).all())


def _shared_node_on_as_template(repo: GraphRepository) -> GraphNode:
    """A node homed at a with a's template, linked by b too (the CCX shape)."""
    repo.create_template(
        TemplateCreate(name="person", template_type="node", source_id="a"), custom_id="a_person"
    )
    node = repo.create_node(
        NodeCreate(template_id="a_person", label="Shared", source_id="a", embedding=[0.1])
    )
    repo.assign_source_to_nodes([node.id], "b", DB)
    row = repo.session.get(GraphNode, node.id)
    assert row is not None
    return row


def test_release_all_sources_leaves_no_template_behind(repo: GraphRepository) -> None:
    """Review repro: the reset used to keep ``a_person`` unowned after its last user went."""
    _shared_node_on_as_template(repo)
    repo.release_all_sources()
    assert _ids(repo, GraphNode) == set()
    assert "a_person" not in _template_ids(repo)


def test_deleting_a_then_b_leaves_no_template_behind(repo: GraphRepository) -> None:
    """Review repro: A's release re-homes ``a_person`` to b, so B's release can delete it."""
    _shared_node_on_as_template(repo)
    repo.release_source("a")
    repo.session.delete(repo.session.get(SourceRow, "a"))
    repo.session.commit()
    repo.session.expire_all()
    template = repo.session.get(GraphTemplate, "a_person")
    assert template is not None
    assert template.source_id == "b"

    repo.release_source("b")
    repo.session.delete(repo.session.get(SourceRow, "b"))
    repo.session.commit()
    assert _ids(repo, GraphNode) == set()
    assert "a_person" not in _template_ids(repo)


def test_a_kept_template_folds_into_the_new_owners_same_named_one(repo: GraphRepository) -> None:
    """Per-source templates share names: re-homing to b where b has "person" too repoints users."""
    node = _shared_node_on_as_template(repo)
    repo.create_template(
        TemplateCreate(name="person", template_type="node", source_id="b"), custom_id="b_person"
    )
    repo.release_source("a")
    repo.session.expire_all()
    assert "a_person" not in _template_ids(repo)
    row = repo.session.get(GraphNode, node.id)
    assert row is not None
    assert row.template_id == "b_person"


def test_a_template_only_unowned_rows_use_is_left_unowned(repo: GraphRepository) -> None:
    """Users with no membership at all (legacy rows): nothing to re-home to, so NULL."""
    repo.create_template(
        TemplateCreate(name="person", template_type="node", source_id="a"), custom_id="a_person"
    )
    repo.create_node(NodeCreate(template_id="a_person", label="Hand-made", source_id=None))
    repo.release_source("a")
    repo.session.expire_all()
    template = repo.session.get(GraphTemplate, "a_person")
    assert template is not None
    assert template.source_id is None


def test_an_unowned_template_folds_into_an_unowned_twin(repo: GraphRepository) -> None:
    """Releasing a, then b, each leaving "person" unowned, leaves one unowned "person".

    The unique key lets NULL owners repeat, so without the fold every release of a source
    whose template only legacy rows use would add another unowned copy of the same template.
    """
    for sid in ("a", "b"):
        repo.create_template(
            TemplateCreate(name="person", template_type="node", source_id=sid),
            custom_id=f"{sid}_person",
        )
        repo.create_node(
            NodeCreate(template_id=f"{sid}_person", label=f"Hand-made {sid}", source_id=None),
            custom_id=f"legacy_{sid}",
        )
    repo.release_source("a")
    repo.release_source("b")
    repo.session.commit()
    repo.session.expire_all()
    unowned = repo.session.exec(select(GraphTemplate).where(GraphTemplate.name == "person")).all()
    assert [(t.id, t.source_id) for t in unowned] == [("a_person", None)]
    for nid in ("legacy_a", "legacy_b"):
        row = repo.session.get(GraphNode, nid)
        assert row is not None and row.template_id == "a_person"


def test_an_unowned_template_never_folds_into_a_system_template(repo: GraphRepository) -> None:
    """A system template is unowned too, but it is not a twin: the user template stays its own.

    Folding into it would repoint the users at the system template and lose the source
    template's description and properties (#795 follow-up N3).
    """
    repo.create_template(
        TemplateCreate(name="person", template_type="node"), custom_id="sys_person", is_system=True
    )
    repo.create_template(
        TemplateCreate(
            name="person", template_type="node", source_id="a", description="a's person"
        ),
        custom_id="a_person",
    )
    repo.create_node(
        NodeCreate(template_id="a_person", label="Hand-made", source_id=None), custom_id="legacy"
    )
    repo.release_source("a")
    repo.session.commit()
    repo.session.expire_all()
    kept = repo.session.get(GraphTemplate, "a_person")
    assert kept is not None, "the user template was folded into the system template"
    assert (kept.source_id, kept.description) == (None, "a's person")
    row = repo.session.get(GraphNode, "legacy")
    assert row is not None and row.template_id == "a_person"
    assert repo.session.get(GraphTemplate, "sys_person") is not None


def _merged_pierre(repo: GraphRepository) -> str:
    """A node homed at a, merged with b's reading: its row carries both sources' names."""
    repo.session.add(_source("c"))
    repo.session.commit()
    pierre = repo.create_node(_node("Pierre", "a", aliases=["the heir"]))
    count = repo.create_node(_node("Count Bezúkhov", "b", aliases=["Petrusha"]))
    repo.absorb_node(pierre.id, count.id)
    repo.delete_node(count.id)
    repo.update_node(
        pierre.id, NodeUpdate(properties={"aliases": ["the heir", "Count Bezúkhov", "Petrusha"]})
    )
    repo.session.commit()
    return pierre.id


def test_a_link_with_no_reading_records_the_nodes_own_pre_merge_reading(
    repo: GraphRepository,
) -> None:
    """Never the merged row: it carries b's names, which c never wrote (#795 follow-up M5)."""
    nid = _merged_pierre(repo)
    repo.assign_source_to_nodes([nid], "c", "test")
    members = repo.node_members(nid)
    assert members["c"]["aliases"] == ["the heir"]
    # the home's reading as it is: no provenance stamped onto it for another source
    assert members["c"]["properties"] == members["a"]["properties"]


def test_a_link_with_a_reading_records_that_reading(repo: GraphRepository) -> None:
    nid = _merged_pierre(repo)
    reading = node_create_snapshot(nid, _node("Pierre", None, aliases=["the bastard son"]))
    repo.assign_source_to_nodes([nid], "c", "test", readings={nid: reading})
    assert repo.node_members(nid)["c"]["aliases"] == ["the bastard son"]


def test_a_ccx_upsert_naming_no_writer_leaves_the_homes_reading_alone(
    repo: GraphRepository,
) -> None:
    """The incoming content is not the home's reading: the importer records it for its writer."""
    nid = _merged_pierre(repo)
    repo.session.exec(  # type: ignore[call-overload]
        text("UPDATE graph_nodes SET ccx_iri = 'urn:x' WHERE id = :id").bindparams(id=nid)
    )
    repo.session.commit()
    before = repo.node_members(nid)
    repo.upsert_node_by_ccx_iri("urn:x", _node("Pierre", None, aliases=["the bastard son"]), "test")
    assert repo.node_members(nid) == before


def test_a_ccx_upsert_naming_no_writer_leaves_even_a_sole_homes_reading_alone(
    repo: GraphRepository,
) -> None:
    """The incoming content may be another package's reading of the same IRI.

    Fix round 1 refreshed the reading of a row its home alone attested: a second
    package citing the same entity then overwrote the first package's reading
    (#795 follow-up, fix round 2). The importer re-records a reading only for a
    source it knows wrote it.
    """
    node = repo.create_node(_node("Pierre", "a", aliases=["the heir"]))
    a = repo.create_node(_node("A", "a"))
    edge = repo.create_edge(_edge(node.id, a.id, "a"))
    repo.session.exec(  # type: ignore[call-overload]
        text("UPDATE graph_nodes SET ccx_iri = 'urn:n' WHERE id = :id").bindparams(id=node.id)
    )
    repo.session.exec(  # type: ignore[call-overload]
        text("UPDATE graph_edges SET ccx_iri = 'urn:e' WHERE id = :id").bindparams(id=edge.id)
    )
    repo.session.commit()
    before_node, before_edge = repo.node_members(node.id), repo.edge_members(edge.id)
    repo.upsert_node_by_ccx_iri("urn:n", _node("Pierre", None, aliases=["the count"]), "test")
    changed = _edge(node.id, a.id, None)
    changed.properties = {"justification": "rewritten"}
    repo.upsert_edge_by_ccx_iri("urn:e", changed, "test")
    assert repo.node_members(node.id) == before_node
    assert repo.edge_members(edge.id) == before_edge


def test_clear_node_embeddings_clears_only_the_given_nodes(repo: GraphRepository) -> None:
    pierre = repo.create_node(_node("Pierre", "a"))
    andrew = repo.create_node(_node("Andrew", "a"))
    assert repo.clear_node_embeddings([pierre.id]) == 1
    cleared = repo.get_node(pierre.id, include_embedding=True)
    kept = repo.get_node(andrew.id, include_embedding=True)
    assert cleared is not None and not cleared.embedding
    assert kept is not None and kept.embedding == [0.1, 0.2]


def test_release_joins_the_callers_transaction(repo: GraphRepository) -> None:
    node = repo.create_node(_node("Pierre", "a"))
    repo.session.commit()
    repo.session._transaction_depth += 1
    try:
        repo.release_source("a")
        repo.session.rollback()
    finally:
        repo.session._transaction_depth -= 1
    assert node.id in _ids(repo, GraphNode)
    assert set(repo.node_members(node.id)) == {"a"}


def test_release_all_sources_leaves_only_sourceless_rows(repo: GraphRepository) -> None:
    a = repo.create_node(_node("A", "a"))
    b = repo.create_node(_node("B", "b"))
    mine = repo.create_node(_node("Hand-made", None))
    repo.create_edge(_edge(a.id, b.id, "a"))
    repo.create_edge(_edge(mine.id, mine.id, None, "self"))
    repo.create_template(
        TemplateCreate(name="person", template_type="node", source_id="b"), custom_id="b_node"
    )

    repo.release_all_sources()

    assert _ids(repo, GraphNode) == {mine.id}
    assert len(_ids(repo, GraphEdge)) == 1
    assert repo.session.get(GraphTemplate, "b_node") is None
    assert repo.session.exec(select(GraphNodeSource)).all() == []


def test_deleting_a_source_row_unhomes_what_it_did_not_release(repo: GraphRepository) -> None:
    """``source_id`` is ``ON DELETE SET NULL`` (D1): the source row never takes graph rows with it."""
    node = repo.create_node(_node("Pierre", "a"))
    tpl = repo.create_template(
        TemplateCreate(name="person", template_type="node", source_id="a"), custom_id="a_tpl"
    )
    repo.session.delete(repo.session.get(SourceRow, "a"))
    repo.session.commit()
    repo.session.expire_all()
    row = repo.session.get(GraphNode, node.id)
    assert row is not None
    assert row.source_id is None
    template = repo.session.get(GraphTemplate, tpl.id)
    assert template is not None
    assert template.source_id is None


def _insert_as_of_db(session: Session, row: SQLModel) -> None:
    """Insert ``row`` with the columns the live table has, not the ones the model has.

    These tests build a database at revision 0018 and write rows into it, but the models
    are at head: a column a later revision adds (0020's ``sources.memory_by``) is not in the
    0018 table, and ``session.add`` would name it. The row's other values, Python defaults
    included, go in as they are.
    """
    mapper = sa.inspect(type(row))
    table = mapper.local_table
    conn = session.connection()
    live = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table.name})")}
    values = {
        column.name: getattr(row, attr.key)
        for attr in mapper.column_attrs
        for column in attr.columns
        if column.name in live
    }
    conn.execute(sa.insert(table).values(**values))


# ---------------------------------------------------------------------------
# Migration 0019
# ---------------------------------------------------------------------------


def _count(db: Path, table: str) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def test_migration_backfills_one_membership_per_row(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0018")
        with Session(get_engine(db)) as session:
            _insert_as_of_db(session, _source("a"))
            _insert_as_of_db(session, _source("b"))
            _insert_as_of_db(
                session,
                GraphTemplate(id="t_node", database_name=DB, name="thing", template_type="node"),
            )
            _insert_as_of_db(
                session,
                GraphTemplate(id="t_edge", database_name=DB, name="rel", template_type="edge"),
            )
            session.commit()
            for nid, label, sid, props in (
                ("n1", "Pierre", "a", {"aliases": ["P."], "aliases_given": ["Pierre"]}),
                ("n2", "Natasha", "b", {}),
                ("n3", "Nobody", None, {}),
            ):
                _insert_as_of_db(
                    session,
                    GraphNode(
                        id=nid,
                        database_name=DB,
                        graph_name="knowledge",
                        template_id="t_node",
                        label=label,
                        entity_type="character",
                        properties=props,
                        source_id=sid,
                    ),
                )
            session.commit()
            _insert_as_of_db(
                session,
                GraphEdge(
                    id="e1",
                    database_name=DB,
                    template_id="t_edge",
                    source_node_id="n1",
                    target_node_id="n2",
                    label="loves",
                    properties={"since": "1805"},
                    source_id="a",
                ),
            )
            session.commit()
        # Homes a membership cannot name: '' and a source row that is gone (written with the
        # foreign keys off, as the 0019 backfill itself runs). Plus enough rows for several
        # streamed batches.
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executemany(
            "INSERT INTO graph_nodes (id, database_name, graph_name, template_id, label, "
            "properties, source_id, created_at, updated_at) "
            "VALUES (?, ?, 'knowledge', 't_node', ?, '{}', ?, '2026-10-18', '2026-10-18')",
            [("n_empty", DB, "Empty home", ""), ("n_ghost", DB, "Ghost home", "ghost")]
            + [(f"bulk{i:04d}", DB, f"Bulk {i}", "b") for i in range(1200)],
        )
        conn.executemany(
            "INSERT INTO graph_edges (id, database_name, graph_name, template_id, source_node_id, "
            "target_node_id, label, properties, source_id, created_at, updated_at) "
            "VALUES (?, ?, 'knowledge', 't_edge', 'n1', 'n2', 'knows', '{}', ?, "
            "'2026-10-18', '2026-10-18')",
            [("e_empty", DB, ""), ("e_ghost", DB, "ghost")],
        )
        conn.commit()
        conn.close()
        evict_engine(db)

        upgrade_to(db, "0019")

        # n3 has no source; n_empty and n_ghost name none that exists
        assert _count(db, "graph_node_sources") == 2 + 1200
        assert _count(db, "graph_edge_sources") == 1
        conn = sqlite3.connect(str(db))
        try:
            orphans = conn.execute(
                "SELECT COUNT(*) FROM graph_node_sources WHERE source_id NOT IN "
                "(SELECT id FROM sources) UNION ALL SELECT COUNT(*) FROM graph_edge_sources "
                "WHERE source_id NOT IN (SELECT id FROM sources)"
            ).fetchall()
        finally:
            conn.close()
        assert orphans == [(0,), (0,)], "a membership names no source row"
        # the models are at head; read the backfilled rows through them at head
        evict_engine(db)
        upgrade_to_head(db)
        with SafeSession(get_engine(db)) as session:
            repo = GraphRepository(session, database_name=DB)
            n1 = session.get(GraphNode, "n1")
            e1 = session.get(GraphEdge, "e1")
            assert n1 is not None
            assert e1 is not None
            assert repo.node_members("n1") == {"a": node_snapshot(n1)}
            assert repo.node_members("n1")["a"]["aliases_given"] == ["Pierre"]
            assert repo.node_members("n2")["b"]["label"] == "Natasha"
            assert repo.node_members("n3") == {}
            assert repo.edge_members("e1") == {"a": edge_snapshot(e1)}
    finally:
        evict_engine(db)


def test_0019_downgrade_drops_the_tables(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0019")
        downgrade_to(db, "0018")
        conn = sqlite3.connect(str(db))
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        finally:
            conn.close()
        assert "graph_node_sources" not in names
        assert "graph_edge_sources" not in names
        assert "graph_nodes" in names
    finally:
        evict_engine(db)


_HOMED = ("graph_nodes", "graph_edges", "graph_templates")


def _source_fk_and_index(db: Path, table: str) -> tuple[str, bool]:
    """The ``ON DELETE`` rule of ``table.source_id`` and whether its index is there."""
    conn = sqlite3.connect(str(db))
    try:
        rules = [
            r[6]
            for r in conn.execute(f"PRAGMA foreign_key_list({table})")
            if r[3] == "source_id" and r[2] == "sources"
        ]
        indexed = any(
            "source_id" in [c[2] for c in conn.execute(f"PRAGMA index_info('{ix[1]}')")]
            for ix in conn.execute(f"PRAGMA index_list({table})")
        )
    finally:
        conn.close()
    assert len(rules) == 1, (table, rules)
    return rules[0], indexed


def _schema(db: Path) -> dict[str, object]:
    """Every foreign key and index of the three homed tables, as SQLite reports them."""
    conn = sqlite3.connect(str(db))
    try:
        return {
            table: {
                "fks": sorted(
                    (r[3], r[2], r[4], r[6])
                    for r in conn.execute(f"PRAGMA foreign_key_list({table})")
                ),
                "indexes": sorted(
                    (
                        ix[1],
                        ix[2],
                        tuple(c[2] for c in conn.execute(f"PRAGMA index_info('{ix[1]}')")),
                    )
                    for ix in conn.execute(f"PRAGMA index_list({table})")
                ),
            }
            for table in _HOMED
        }
    finally:
        conn.close()


def _rows(db: Path) -> dict[str, list[tuple[object, ...]]]:
    conn = sqlite3.connect(str(db))
    try:
        return {
            t: sorted(conn.execute(f"SELECT id, source_id FROM {t}").fetchall()) for t in _HOMED
        }
    finally:
        conn.close()


def _delete_source_row(db: Path, source_id: str) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        for table in ("graph_node_sources", "graph_edge_sources"):
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone():
                conn.execute(f"DELETE FROM {table} WHERE source_id = ?", (source_id,))
        conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        conn.commit()
    finally:
        conn.close()


def _seed_0018(db: Path) -> None:
    """Two sources, each with a template, two nodes and an edge, written at 0018."""
    with Session(get_engine(db)) as session:
        for sid in ("a", "b"):
            _insert_as_of_db(session, _source(sid))
        session.commit()
        for sid in ("a", "b"):
            _insert_as_of_db(
                session,
                GraphTemplate(
                    id=f"t_{sid}",
                    database_name=DB,
                    name="thing",
                    template_type="node",
                    source_id=sid,
                ),
            )
        session.commit()
        for sid in ("a", "b"):
            for n in ("1", "2"):
                _insert_as_of_db(
                    session,
                    GraphNode(
                        id=f"{sid}{n}",
                        database_name=DB,
                        graph_name="knowledge",
                        template_id=f"t_{sid}",
                        label=f"{sid}{n}",
                        source_id=sid,
                    ),
                )
        session.commit()
        for sid in ("a", "b"):
            _insert_as_of_db(
                session,
                GraphEdge(
                    id=f"e_{sid}",
                    database_name=DB,
                    template_id=f"t_{sid}",
                    source_node_id=f"{sid}1",
                    target_node_id=f"{sid}2",
                    label="knows",
                    source_id=sid,
                ),
            )
        session.commit()
    evict_engine(db)


def test_0019_unhomes_instead_of_cascading_and_downgrade_restores_it(tmp_path: Path) -> None:
    """Rows survive the rebuild; only the source_id rule changes, both ways (review fix)."""
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    try:
        upgrade_to(db, "0018")
        _seed_0018(db)
        before_schema, before_rows = _schema(db), _rows(db)
        assert {t: _source_fk_and_index(db, t) for t in _HOMED} == dict.fromkeys(
            _HOMED, ("CASCADE", True)
        )

        upgrade_to(db, "0019")
        evict_engine(db)
        assert _rows(db) == before_rows  # every row kept through the table rebuild
        after = _schema(db)
        assert {t: _source_fk_and_index(db, t) for t in _HOMED} == dict.fromkeys(
            _HOMED, ("SET NULL", True)
        )
        for table in _HOMED:
            assert after[table]["indexes"] == before_schema[table]["indexes"], table
            # every other foreign key unchanged: edge -> node CASCADE, node/edge -> template RESTRICT
            other = [fk for fk in after[table]["fks"] if fk[0] != "source_id"]
            assert other == [fk for fk in before_schema[table]["fks"] if fk[0] != "source_id"]
        assert ("source_node_id", "graph_nodes", "id", "CASCADE") in after["graph_edges"]["fks"]
        assert ("target_node_id", "graph_nodes", "id", "CASCADE") in after["graph_edges"]["fks"]
        assert ("template_id", "graph_templates", "id", "RESTRICT") in after["graph_edges"]["fks"]
        assert ("template_id", "graph_templates", "id", "RESTRICT") in after["graph_nodes"]["fks"]

        _delete_source_row(db, "a")  # the source row alone: its rows stay, unhomed
        rows = _rows(db)
        assert ("a1", None) in rows["graph_nodes"]
        assert ("e_a", None) in rows["graph_edges"]
        assert ("t_a", None) in rows["graph_templates"]

        downgrade_to(db, "0018")
        evict_engine(db)
        assert _schema(db) == before_schema  # CASCADE back, indexes and other FKs as they were
        assert ("b1", "b") in _rows(db)["graph_nodes"]
        # and the cascade takes b's rows again (its template unowned first: a cascading
        # template delete would hit the node -> template RESTRICT, as before 0019)
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("UPDATE graph_templates SET source_id = NULL WHERE id = 't_b'")
            conn.commit()
        finally:
            conn.close()
        _delete_source_row(db, "b")
        rows = _rows(db)
        assert all(sid != "b" for t in _HOMED for _id, sid in rows[t])
        assert "b1" not in {i for i, _ in rows["graph_nodes"]}
    finally:
        evict_engine(db)
