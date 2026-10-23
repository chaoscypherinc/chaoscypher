# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Applying a resolution plan to a real SQLite graph: merges, alias drops, flags."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, select

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    DocumentChunk,
    GraphEdge,
    RelationshipCitation,
    SourceCitation,
    SourceRow,
)
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.models import EdgeCreate, NodeCreate, TemplateCreate
from chaoscypher_core.services.graph.resolution import resolve_graph
from chaoscypher_core.services.graph.resolution.apply import (
    FLAG_KEY,
    RESOLVED_FROM,
    ResolutionReport,
    apply_resolution,
    load_graph,
)
from chaoscypher_core.services.graph.resolution.plan import (
    AliasDrop,
    EdgeFlag,
    Merge,
    ResolutionPlan,
)
from tests.fakes.domain_tables import domain_conventions


LITERARY = domain_conventions("literary")
"""The literary domain's naming conventions, the ones a Book One graph is resolved under."""


@pytest.fixture
def repo(tmp_path: Path) -> Generator[GraphRepository]:
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="test")
    adapter.connect()
    repo = GraphRepository(adapter.session, database_name="test")
    for tpl, kind in (("character", "node"), ("rel", "edge")):
        repo.create_template(TemplateCreate(name=tpl, template_type=kind), custom_id=tpl)
    yield repo
    adapter.disconnect()


def _node(repo: GraphRepository, nid: str, label: str, **props: object) -> str:
    repo.create_node(
        NodeCreate(template_id="character", label=label, entity_type="character", properties=props),
        custom_id=nid,
    )
    return nid


def _edge(repo: GraphRepository, eid: str, src: str, tgt: str, label: str, **props: object) -> str:
    repo.create_edge(
        EdgeCreate(
            template_id="rel", source_node_id=src, target_node_id=tgt, label=label, properties=props
        ),
        custom_id=eid,
    )
    return eid


def _seed(repo: GraphRepository) -> None:
    _node(
        repo,
        "anna",
        "Anna Pávlovna Schérer",
        aliases=["Annette", "Anna Mikháylovna"],
        description="The hostess.",
    )
    _node(
        repo,
        "mlle",
        "Mlle Schérer",
        aliases=["the hostess"],
        description="Hostess of the soirée in Petersburg.",
        rank="maid of honour",
    )
    _node(repo, "drub", "Princess Anna Mikháylovna Drubetskáya", aliases=[])
    _node(repo, "pierre", "Pierre", aliases=[])
    _node(repo, "vasili", "Prince Vasíli Kurágin", aliases=[])
    _node(repo, "nicholas", "Nicholas", aliases=[])
    _edge(repo, "e1", "mlle", "pierre", "interacts_with", justification="She greets Pierre.")
    _edge(repo, "e2", "vasili", "mlle", "interacts_with", justification="He arrives first.")
    _edge(
        repo, "e3", "vasili", "anna", "interacts_with", justification="He arrives first."
    )  # already on the kept node
    _edge(repo, "e4", "anna", "mlle", "interacts_with", justification="would loop")
    _edge(
        repo,
        "e5",
        "nicholas",
        "pierre",
        "sibling_of",
        justification="Nicholas is the count's eldest son and Pétya is his youngest boy.",
    )


def test_a_merge_folds_the_dropped_node_moves_its_edges_and_keeps_provenance(
    repo: GraphRepository,
) -> None:
    _seed(repo)
    plan = ResolutionPlan(
        merges=[Merge(keep="anna", drop="mlle", why="test")],
        alias_drops=[AliasDrop(node="anna", alias="Anna Mikháylovna", owner="drub")],
        flags=[EdgeFlag(edge="e5", why="names Pétya")],
    )
    report = apply_resolution(repo, plan)
    assert report.merged == [("Anna Pávlovna Schérer", "Mlle Schérer")]
    assert (report.edges_moved, report.edges_dropped) == (
        1,
        2,
    )  # e1 moved; e2 a copy of e3; e4 a loop
    assert report.aliases_dropped == []  # the drop fell inside the merge: applied through it
    assert report.flagged == 1

    assert repo.get_node("mlle") is None
    anna = repo.get_node("anna")
    assert anna is not None
    assert anna.properties["aliases"] == ["Annette", "Mlle Schérer", "the hostess"]
    assert anna.properties[RESOLVED_FROM] == ["Mlle Schérer"]
    assert anna.properties["rank"] == "maid of honour"  # a property only the dropped node had
    assert (
        # descriptions follow the deduplication's own policy: both kept when they differ
        anna.properties["description"] == "The hostess. Hostess of the soirée in Petersburg."
    )

    nodes, edges = load_graph(repo)
    assert {n["id"] for n in nodes} == {"anna", "drub", "pierre", "vasili", "nicholas"}
    moved = [e for e in edges if e["properties"].get(RESOLVED_FROM) == "mlle"]
    assert [(e["source"], e["target"], e["label"]) for e in moved] == [
        ("anna", "pierre", "interacts_with")
    ]
    assert not any(e["source"] == "mlle" or e["target"] == "mlle" for e in edges)
    flagged = next(e for e in edges if e["id"] == "e5")
    assert flagged["properties"][FLAG_KEY] == "names Pétya"
    assert flagged["label"] == "sibling_of"  # flagged, not deleted


def test_a_merge_keeps_both_definitions_the_commit_wrote(repo: GraphRepository) -> None:
    """The Book One case: the kept Drubetskáya's "Rostóv household" text and her twin's."""
    _node(
        repo,
        "full",
        "Princess Anna Mikháylovna Drubetskáya",
        aliases=[],
        definition="A member of the Rostóv household who assists the countess.",
    )
    _node(
        repo,
        "twin",
        "Princess Drubetskáya",
        aliases=[],
        definition="An elderly lady from one of the best families in Russia who has become poor.",
    )
    resolve_graph(repo, conventions=LITERARY)
    full = repo.get_node("full")
    assert full is not None
    assert "Rostóv household" in full.properties["definition"]
    assert "elderly lady" in full.properties["definition"]


def test_an_alias_drop_whose_owner_is_the_kept_node_stays_on_it(repo: GraphRepository) -> None:
    """The Book One case: the dropped twin carried "Anna Mikháylovna", which names the kept node."""
    _node(repo, "full", "Princess Anna Mikháylovna Drubetskáya", aliases=["Anna Mikháylovna"])
    _node(
        repo,
        "twin",
        "Princess Drubetskáya",
        aliases=["Anna Mikháylovna", "Anna Mikháylovna Drubetskáya"],
    )
    _node(repo, "anna", "Anna Pávlovna Schérer", aliases=["Anna Mikháylovna"])
    plan, _ = resolve_graph(repo, conventions=LITERARY)
    assert {(d.node, d.alias) for d in plan.alias_drops} == {
        ("twin", "Anna Mikháylovna"),
        ("twin", "Anna Mikháylovna Drubetskáya"),
        ("anna", "Anna Mikháylovna"),
    }
    full = repo.get_node("full")
    assert full is not None
    assert full.properties["aliases"] == [
        "Anna Mikháylovna",
        "Princess Drubetskáya",
        "Anna Mikháylovna Drubetskáya",
    ]
    anna = repo.get_node("anna")
    assert anna is not None and anna.properties["aliases"] == []


def test_an_alias_drop_on_a_surviving_node_rewrites_its_list(repo: GraphRepository) -> None:
    _seed(repo)
    report = apply_resolution(
        repo, ResolutionPlan(alias_drops=[AliasDrop("anna", "Anna Mikháylovna", "drub")])
    )
    assert report.aliases_dropped == [("Anna Pávlovna Schérer", "Anna Mikháylovna")]
    anna = repo.get_node("anna")
    assert anna is not None and anna.properties["aliases"] == ["Annette"]


def test_an_alias_drop_reembeds_the_node_as_it_reads_after_the_drop(repo: GraphRepository) -> None:
    """The re-embed hook gets the rewritten node: embedding the old one kept the dropped name."""
    _seed(repo)
    seen: list[list[str]] = []
    apply_resolution(
        repo,
        ResolutionPlan(alias_drops=[AliasDrop("anna", "Anna Mikháylovna", "drub")]),
        reembed=lambda n: seen.append(list(n.properties["aliases"])),
    )
    assert seen == [["Annette"]]


def test_resolve_graph_plans_and_applies_in_one_call_and_is_idempotent(
    repo: GraphRepository,
) -> None:
    _seed(repo)
    reembedded: list[str] = []
    plan, report = resolve_graph(
        repo, conventions=LITERARY, reembed=lambda n: reembedded.append(n.id)
    )
    assert [(m.keep, m.drop) for m in plan.merges] == [("anna", "mlle")]
    assert [(d.node, d.alias) for d in plan.alias_drops] == [("anna", "Anna Mikháylovna")]
    assert [f.edge for f in plan.flags] == ["e5"]
    assert (
        report.summary()
        == "1 merged, 1 edges moved, 2 dropped (1 folded), 0 aliases dropped, 1 flagged"
    )
    assert reembedded == ["anna"]
    anna = repo.get_node("anna")
    assert anna is not None and "Anna Mikháylovna" not in anna.properties["aliases"]

    again, report2 = resolve_graph(repo, conventions=LITERARY)
    assert again.merges == [] and again.alias_drops == []
    assert (
        report2.summary()
        == "0 merged, 0 edges moved, 0 dropped (0 folded), 0 aliases dropped, 0 flagged"
    )


def test_a_merge_keeps_the_kept_nodes_given_aliases_and_never_gives_it_the_dropped_names(
    repo: GraphRepository,
) -> None:
    """The dropped label and aliases are acquired: ``aliases_given`` stays the kept node's (audit W2-1)."""
    _node(
        repo,
        "anna",
        "Anna Pávlovna Schérer",
        aliases=["Annette", "Mlle Schérer"],
        aliases_given=["Annette"],
    )
    _node(repo, "mlle", "Mlle Schérer", aliases=["the hostess"], aliases_given=["the hostess"])
    plan, _report = resolve_graph(repo, conventions=LITERARY)
    assert [(m.keep, m.drop) for m in plan.merges] == [("anna", "mlle")]
    anna = repo.get_node("anna")
    assert anna is not None
    assert set(anna.properties["aliases"]) == {"Annette", "Mlle Schérer", "the hostess"}
    assert anna.properties["aliases_given"] == ["Annette"]


def test_a_merge_refreshes_only_the_changed_nodes_search_rows_when_a_search_repository_is_given(
    repo: GraphRepository,
) -> None:
    """Keyword seeds read the node full-text rows: after a merge they must follow the graph (W2-11).

    Only the nodes the resolution changed: the merged-away node's rows go, the
    kept node's full-text row is rewritten; no whole-table rebuild (#795 follow-up).
    """
    _seed(repo)
    calls: list[tuple[str, list[str]]] = []
    search = SimpleNamespace(
        delete_nodes_batch=lambda ids: calls.append(("delete", list(ids))),
        refresh_node_fulltext=lambda ids: calls.append(("refresh", list(ids))),
    )
    _plan, report = resolve_graph(repo, conventions=LITERARY, reembed=None, search=search)  # type: ignore[arg-type]
    assert calls == [
        ("delete", sorted(report.removed_node_ids)),
        ("refresh", sorted(report.changed_node_ids)),
    ]
    assert report.removed_node_ids == {"mlle"}
    assert "anna" in report.changed_node_ids
    assert "pierre" not in report.changed_node_ids
    calls.clear()
    resolve_graph(repo, conventions=LITERARY, search=search)  # type: ignore[arg-type]
    assert calls == []  # nothing changed the second time: nothing to refresh


def test_resolve_graph_needs_the_graphs_conventions(repo: GraphRepository) -> None:
    """No default: a caller that names no domain tables is a mistake, not the universal set."""
    with pytest.raises(ValidationError):
        resolve_graph(repo)


_TWINS = (
    "A wealthy heir who inherits his father's fortune.",
    "Stout young man with spectacles, back from abroad.",
    "Guest at the soirée who argues about Napoleon.",
)
"""Three descriptions of one name that share almost no words: a merge keeps all three."""


def _twins_merged(tmp_path: Path, ids: tuple[str, ...]) -> tuple[str, str]:
    """Resolve three ``Pierre Bezúkhov`` nodes carrying ``_TWINS`` under ``ids``: the survivor's label and text."""
    db_path = tmp_path / "-".join(ids) / "app.db"
    db_path.parent.mkdir(parents=True)
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="test")
    adapter.connect()
    try:
        repo = GraphRepository(adapter.session, database_name="test")
        for tpl, kind in (("character", "node"), ("rel", "edge")):
            repo.create_template(TemplateCreate(name=tpl, template_type=kind), custom_id=tpl)
        for nid, text in zip(ids, _TWINS, strict=True):
            _node(repo, nid, "Pierre Bezúkhov", aliases=[], definition=text)
        plan, _report = resolve_graph(repo, conventions=LITERARY)
        assert len(plan.merges) == 2
        (survivor,) = load_graph(repo)[0]
        return survivor["label"], survivor["properties"]["definition"]
    finally:
        adapter.disconnect()


def test_records_alike_in_every_name_merge_their_descriptions_in_one_order(tmp_path: Path) -> None:
    """Audit wave 8: a tie between identical records fell to the node id.

    Which twin kept its node and the order the others' descriptions folded in
    followed the ids, so one graph's merged description read differently in
    two databases (and reached the chat context so). The description is now
    part of what orders the members (``record_order_key``), and the dropped
    nodes fold in that order whatever the ids.
    """
    from itertools import permutations

    results = {_twins_merged(tmp_path, ids) for ids in permutations(("n1", "n2", "n3"))}
    assert len(results) == 1
    ((label, text),) = results
    assert label == "Pierre Bezúkhov"
    assert all(part.rstrip(".") in text for part in _TWINS)


def test_a_merged_nodes_own_name_survives_an_alias_drop_of_the_same_text(
    repo: GraphRepository,
) -> None:
    """A dropped node's label is always the kept node's alias (2026-10-07: names are never lost).

    An alias drop planned on the group takes off a name that is someone else's;
    the node folded in is this node, so its own name stays.
    """
    _node(repo, "pierre", "Pierre", aliases=["Monsieur Pierre", "Pétya"])
    _node(repo, "petya", "Pétya", aliases=["little Pétya"])
    _node(repo, "rostov", "Pétya Rostóv")
    apply_resolution(
        repo,
        ResolutionPlan(
            merges=[Merge(keep="pierre", drop="petya", why="alias")],
            alias_drops=[AliasDrop("pierre", "Pétya", "rostov")],
        ),
    )
    pierre = repo.get_node("pierre")
    assert pierre is not None
    assert pierre.properties["aliases"] == ["Monsieur Pierre", "Pétya", "little Pétya"]
    assert pierre.properties["merged_names"] == ["Pétya"]


# -- keep, flag, never delete (2026-10-07): a duplicate edge's justification is kept ----------


def _duplicate_pair(
    repo: GraphRepository, kept: dict[str, object], dropped: dict[str, object]
) -> None:
    """Two nodes of one person, each with an edge to Pierre of the same label."""
    _node(repo, "anna", "Anna Pávlovna Schérer", aliases=[])
    _node(repo, "mlle", "Mlle Schérer", aliases=[])
    _node(repo, "pierre", "Pierre", aliases=[])
    _edge(repo, "kept", "anna", "pierre", "interacts_with", **kept)
    _edge(repo, "dropped", "mlle", "pierre", "interacts_with", **dropped)


def _merged_edge(repo: GraphRepository) -> dict[str, object]:
    plan = ResolutionPlan(merges=[Merge(keep="anna", drop="mlle", why="t")])
    report = apply_resolution(repo, plan)
    assert (report.edges_moved, report.edges_dropped, report.edges_folded) == (0, 1, 1)
    _, edges = load_graph(repo)
    (edge,) = [e for e in edges if e["source"] == "anna" and e["target"] == "pierre"]
    return dict(edge["properties"])


def test_a_duplicate_edge_folds_its_justification_into_the_kept_edge(
    repo: GraphRepository,
) -> None:
    """The kept edge keeps its own justification and gains the dropped copy's, in order."""
    _duplicate_pair(
        repo,
        {"justification": "Anna Pávlovna greeted Pierre with a nod.", "sent_ref": "S3"},
        {"justification": "Mlle Schérer watched Pierre at the aunt's.", "sent_ref": "S9"},
    )
    props = _merged_edge(repo)
    assert (props["justification"], props["sent_ref"]) == (
        "Anna Pávlovna greeted Pierre with a nod.",
        "S3",
    )
    assert props["justifications"] == [
        "Anna Pávlovna greeted Pierre with a nod.",
        "Mlle Schérer watched Pierre at the aunt's.",
    ]


def test_an_evidenced_duplicate_clears_the_unevidenced_flag_and_leads(
    repo: GraphRepository,
) -> None:
    """Kept copy unevidenced, dropped copy evidenced: the fact is evidenced, its sentence leads."""
    _duplicate_pair(
        repo,
        {
            "justification": "She keeps him by her.",
            "sent_ref": "S2",
            "unevidenced": True,
            "evidence_reason": 'neither "Anna Pávlovna Schérer" nor "Pierre" is named in S2',
        },
        {"justification": "Mlle Schérer greeted Pierre.", "sent_ref": "S5", "chunk_index": 4},
    )
    props = _merged_edge(repo)
    assert "unevidenced" not in props
    assert "evidence_reason" not in props
    assert (props["justification"], props["sent_ref"], props["chunk_index"]) == (
        "Mlle Schérer greeted Pierre.",
        "S5",
        4,
    )
    assert props["justifications"] == ["Mlle Schérer greeted Pierre.", "She keeps him by her."]


def test_two_unevidenced_copies_stay_flagged(repo: GraphRepository) -> None:
    _duplicate_pair(
        repo,
        {"justification": "A.", "unevidenced": True, "evidence_reason": "r1"},
        {"justification": "B.", "unevidenced": True, "evidence_reason": "r2"},
    )
    props = _merged_edge(repo)
    assert (props["unevidenced"], props["evidence_reason"]) == (True, "r1")
    assert props["justifications"] == ["A.", "B."]


# -- #795: a merge carries the dropped node's sources and citations (spec 2026-10-17 D2/D4) ----


def _two_sources(repo: GraphRepository) -> None:
    """Sources a and b, one chunk each, so memberships and citations have their FKs."""
    for sid in ("a", "b"):
        repo.session.add(
            SourceRow(
                id=sid,
                database_name="test",
                filename=f"{sid}.txt",
                filepath=f"/tmp/{sid}.txt",
                file_type="text",
                file_size=1,
                content_hash=f"hash-{sid}",
                status="committed",
            )
        )
    repo.session.commit()
    for sid in ("a", "b"):
        repo.session.add(
            DocumentChunk(
                id=f"chunk_{sid}", database_name="test", source_id=sid, chunk_index=0, content=sid
            )
        )
    repo.session.commit()


def _sourced_node(repo: GraphRepository, nid: str, label: str, sid: str) -> None:
    repo.create_node(
        NodeCreate(template_id="character", label=label, entity_type="character", source_id=sid),
        custom_id=nid,
    )
    repo.session.add(
        SourceCitation(
            id=f"cite_{nid}",
            database_name="test",
            entity_uri=nid,
            entity_label=label,
            source_id=sid,
            chunk_id=f"chunk_{sid}",
            extraction_method="ai_extraction",
        )
    )


def _sourced_edge(
    repo: GraphRepository, eid: str, src: str, tgt: str, label: str, sid: str
) -> None:
    repo.create_edge(
        EdgeCreate(
            template_id="rel",
            source_node_id=src,
            target_node_id=tgt,
            label=label,
            properties={"justification": f"{label} per {sid}"},
            source_id=sid,
        ),
        custom_id=eid,
    )
    repo.session.add(
        RelationshipCitation(
            id=f"rcite_{eid}",
            database_name="test",
            edge_id=eid,
            edge_label=label,
            source_entity_label=src,
            target_entity_label=tgt,
            source_id=sid,
            chunk_id=f"chunk_{sid}",
            extraction_method="ai_extraction",
        )
    )


def _cross_source_merge(repo: GraphRepository) -> ResolutionReport:
    """Pierre of a keeps Pierre Bezúkhov of b: one edge moves, one folds, one would loop."""
    _two_sources(repo)
    _sourced_node(repo, "a_pierre", "Pierre", "a")
    _sourced_node(repo, "a_natasha", "Natasha", "a")
    _sourced_node(repo, "b_pierre", "Pierre Bezúkhov", "b")
    _sourced_node(repo, "b_dolokhov", "Dolokhov", "b")
    _sourced_edge(repo, "a_loves", "a_pierre", "a_natasha", "loves", "a")
    _sourced_edge(repo, "b_duels", "b_pierre", "b_dolokhov", "duels", "b")  # moves
    _sourced_edge(repo, "b_loves", "b_pierre", "a_natasha", "loves", "b")  # folds into a_loves
    _sourced_edge(repo, "b_loop", "a_pierre", "b_pierre", "is", "b")  # loops once merged
    repo.session.commit()
    report = apply_resolution(
        repo, ResolutionPlan(merges=[Merge(keep="a_pierre", drop="b_pierre", why="t")])
    )
    repo.session.commit()
    assert (report.edges_moved, report.edges_dropped, report.edges_folded) == (1, 2, 1)
    return report


def test_merge_moves_memberships_and_citations(repo: GraphRepository) -> None:
    _cross_source_merge(repo)
    assert set(repo.node_members("a_pierre")) == {"a", "b"}
    assert repo.node_members("a_pierre")["b"]["label"] == "Pierre Bezúkhov"  # what b wrote

    cites = {c.id: c.entity_uri for c in repo.session.exec(select(SourceCitation)).all()}
    assert "b_pierre" not in cites.values()
    assert cites["cite_b_pierre"] == "a_pierre"

    edges = {e.id for e in repo.session.exec(select(GraphEdge)).all()}
    rcites = {c.id: c.edge_id for c in repo.session.exec(select(RelationshipCitation)).all()}
    assert set(rcites.values()) <= edges, rcites
    assert rcites["rcite_b_loves"] == "a_loves"  # folded: the kept copy carries its citation
    assert set(repo.edge_members("a_loves")) == {"a", "b"}


def test_moved_edge_keeps_its_home_source(repo: GraphRepository) -> None:
    _cross_source_merge(repo)
    (moved,) = repo.session.exec(select(GraphEdge).where(GraphEdge.label == "duels")).all()
    assert moved.source_node_id == "a_pierre"
    assert moved.source_id == "b"  # its home, not None: the edge is still what b wrote
    assert set(repo.edge_members(moved.id)) == {"b"}
    (rcite,) = repo.session.exec(
        select(RelationshipCitation).where(RelationshipCitation.id == "rcite_b_duels")
    ).all()
    assert rcite.edge_id == moved.id


def test_self_loop_citations_are_dropped(repo: GraphRepository) -> None:
    _cross_source_merge(repo)
    assert repo.get_edge("b_loop") is None
    assert (
        repo.session.exec(
            select(RelationshipCitation).where(RelationshipCitation.id == "rcite_b_loop")
        ).first()
        is None
    )


def test_a_merge_reports_every_edge_row_it_took_off_the_graph(repo: GraphRepository) -> None:
    """Moved (re-created under a new id), folded and looped: a release reports them deleted."""
    report = _cross_source_merge(repo)
    assert report.removed_edge_ids == {"b_duels", "b_loves", "b_loop"}
    assert not ({"b_duels", "b_loves", "b_loop"} & {e.id for e in repo.list_edges(limit=100)})
