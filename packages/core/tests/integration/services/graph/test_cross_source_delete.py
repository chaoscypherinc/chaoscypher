# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Acceptance for #795: identity resolution merges across sources, source delete assumes one source per node.

Real SQLite db, real ``SqliteAdapter`` + ``GraphRepository`` on one session, real
``SourceCommitService._resolve_identities`` (the post-commit pass) and the real
``SourceService.delete_source``. The graph mimics what two commits write: per-source
templates, nodes/edges stamped with their ``source_id``, entity citations whose
``entity_uri`` is the node id and relationship citations keyed by ``edge_id``.

Source A (book): "Pierre" -loves-> "Natasha Rostova"
Source B (report): "Pierre Bezúkhov" -duels-> "Dolokhov", "Pierre Bezúkhov" -knows-> "Natasha"
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import SQLModel, select

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    DocumentChunk,
    GraphEdge,
    GraphEdgeSource,
    GraphNode,
    GraphNodeSource,
    GraphTemplate,
    RelationshipCitation,
    SourceCitation,
    SourceRow,
)
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.models import EdgeCreate, NodeCreate, SourceStatus, TemplateCreate
from chaoscypher_core.services.graph.management.release import refresh_after_release
from chaoscypher_core.services.graph.management.source import SourceService
from chaoscypher_core.services.reset.data_reset import DataResetService
from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService
from chaoscypher_core.services.sources.management.re_extraction import force_re_extract
from chaoscypher_core.settings import EngineSettings


DB = "test"

NODES = {  # id -> (label, source)
    "a_pierre": ("Pierre", "a"),
    "a_natasha": ("Natasha Rostova", "a"),
    "b_pierre": ("Pierre Bezúkhov", "b"),
    "b_dolokhov": ("Dolokhov", "b"),
    "b_natasha": ("Natasha", "b"),
}
EDGES = {  # id -> (src, tgt, label, source)
    "a_loves": ("a_pierre", "a_natasha", "loves", "a"),
    "b_duels": ("b_pierre", "b_dolokhov", "duels", "b"),
    "b_knows": ("b_pierre", "b_natasha", "knows", "b"),
}


def _write_source_graph(
    adapter: SqliteAdapter, repo: GraphRepository, sid: str, prefix: str = ""
) -> None:
    """What one commit writes for source ``sid``: its templates, nodes, edges and citations.

    ``prefix`` is put on every id, as a re-commit writes fresh ids.
    """
    tpl = {"node": f"{prefix}character_{sid}", "edge": f"{prefix}rel_{sid}"}
    repo.create_template(
        TemplateCreate(name="character", template_type="node", source_id=sid),
        custom_id=tpl["node"],
    )
    repo.create_template(
        TemplateCreate(name=f"rel_{sid}", template_type="edge", source_id=sid),
        custom_id=tpl["edge"],
    )
    for nid, (label, nsid) in NODES.items():
        if nsid != sid:
            continue
        repo.create_node(
            NodeCreate(
                template_id=tpl["node"],
                label=label,
                entity_type="character",
                properties={"aliases": [], "description": f"{label} as source {sid} tells it"},
                source_id=sid,
            ),
            custom_id=prefix + nid,
        )
        adapter.create_citation(
            {
                "id": f"{prefix}cite_{nid}",
                "database_name": DB,
                "entity_uri": prefix + nid,
                "entity_label": label,
                "entity_type": "character",
                "source_id": sid,
                "chunk_id": f"chunk_{sid}",
                "extraction_method": "ai_extraction",
            }
        )
    for eid, (s, t, label, esid) in EDGES.items():
        if esid != sid:
            continue
        repo.create_edge(
            EdgeCreate(
                template_id=tpl["edge"],
                source_node_id=prefix + s,
                target_node_id=prefix + t,
                label=label,
                properties={"justification": f"{label} per {sid}"},
                source_id=sid,
            ),
            custom_id=prefix + eid,
        )
        adapter.create_relationship_citations_batch(
            [
                {
                    "id": f"{prefix}rcite_{eid}",
                    "database_name": DB,
                    "edge_id": prefix + eid,
                    "edge_label": label,
                    "source_entity_label": NODES[s][0],
                    "target_entity_label": NODES[t][0],
                    "source_id": sid,
                    "chunk_id": f"chunk_{sid}",
                    "extraction_method": "ai_extraction",
                }
            ]
        )


def _build(tmp_path: Path, name: str) -> tuple[SqliteAdapter, GraphRepository]:
    db_path = tmp_path / f"{name}.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name=DB)
    adapter.connect()
    for sid in ("a", "b"):
        adapter.create_source(
            {
                "id": sid,
                "database_name": DB,
                "filename": f"{sid}.txt",
                "filepath": str(tmp_path / f"{sid}.txt"),
                "file_type": "text",
                "file_size": 1,
                "content_hash": f"hash-{sid}",
                "status": "committed",
            }
        )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE sources SET forced_domain = 'literary'")
    now = datetime.now(UTC)
    for sid in ("a", "b"):
        adapter.session.add(
            DocumentChunk(
                id=f"chunk_{sid}",
                database_name=DB,
                source_id=sid,
                chunk_index=0,
                content=f"chunk of {sid}",
                created_at=now,
            )
        )
    adapter.session.commit()
    repo = GraphRepository(adapter.session, database_name=DB)
    for sid in ("a", "b"):
        _write_source_graph(adapter, repo, sid)
    adapter.session.commit()
    return adapter, repo


def _commit_service(
    adapter: SqliteAdapter, repo: GraphRepository, *, source_repository: Any = None
) -> SourceCommitService:
    """The real commit service over ``adapter``'s graph (resolution on, a stub embedder)."""
    settings = _settings()
    provider = SimpleNamespace(
        batch_embed=AsyncMock(
            side_effect=lambda texts: SimpleNamespace(embeddings=[[0.1, 0.2]] * len(texts))
        )
    )
    return SourceCommitService(
        graph_repository=repo,
        source_repository=source_repository or MagicMock(),
        sources_repository=adapter,
        indexing_repository=MagicMock(),
        search_repository=MagicMock(),
        settings=settings,
        embedding_provider=provider,  # type: ignore[arg-type]
    )


def _settings() -> EngineSettings:
    settings = EngineSettings()
    settings.graph.resolve_identities = True
    settings.current_database = DB
    return settings


async def _resolve(
    adapter: SqliteAdapter, repo: GraphRepository, scope: set[str] | None = None
) -> None:
    """The real post-commit pass, as B's commit runs it (scope: B's nodes)."""
    svc = _commit_service(adapter, repo)
    if scope is None:
        scope = {n for n, (_, s) in NODES.items() if s == "b"}
    await svc._resolve_identities("b", scope=scope)
    adapter.session.commit()


def _state(adapter: SqliteAdapter) -> dict[str, Any]:
    s = adapter.session
    s.expire_all()
    nodes = {
        n.id: {"label": n.label, "source_id": n.source_id, "props": n.properties}
        for n in s.exec(select(GraphNode)).all()
    }
    edges = {
        e.id: (e.source_node_id, e.label, e.target_node_id, e.source_id, e.template_id)
        for e in s.exec(select(GraphEdge)).all()
    }
    cites = [(c.entity_uri, c.source_id) for c in s.exec(select(SourceCitation)).all()]
    rcites = [(c.edge_id, c.source_id) for c in s.exec(select(RelationshipCitation)).all()]
    return {"nodes": nodes, "edges": edges, "cites": cites, "rcites": rcites}


def _dump(tag: str, st: dict[str, Any]) -> None:
    print(f"\n==== {tag}")
    for nid, n in sorted(st["nodes"].items()):
        p = n["props"] or {}
        print(
            f"  node {nid:11} src={n['source_id']} label={n['label']!r} "
            f"aliases={p.get('aliases')} resolved_from={p.get('resolved_from')} desc={p.get('description')!r}"
        )
    for eid, e in sorted(st["edges"].items()):
        print(f"  edge {eid[:11]:11} {e[0]} -{e[1]}-> {e[2]} src={e[3]} template={e[4]}")
    live = set(st["nodes"])
    live_e = set(st["edges"])
    print(
        "  entity citations:", [(u, s, "OK" if u in live else "DANGLING") for u, s in st["cites"]]
    )
    print(
        "  rel citations:   ",
        [(u, s, "OK" if u in live_e else "DANGLING") for u, s in st["rcites"]],
    )


async def _merged(tmp_path: Path, name: str) -> tuple[SqliteAdapter, GraphRepository]:
    adapter, repo = _build(tmp_path, name)
    _dump(f"{name}: before resolution", _state(adapter))
    await _resolve(adapter, repo)
    st = _state(adapter)
    _dump(f"{name}: after resolution", st)
    return adapter, repo


def _delete(adapter: SqliteAdapter, repo: GraphRepository, sid: str) -> Exception | None:
    """The real delete; it releases through a repository on ``adapter``'s session (``repo``'s)."""
    assert repo.session is adapter.session
    svc = SourceService(repository=adapter, database_name=DB)  # type: ignore[arg-type]
    try:
        assert svc.delete_source(sid).deleted is True
    except Exception as exc:
        print(f"  delete_source({sid!r}) RAISED {type(exc).__name__}: {str(exc)[:300]}")
        adapter.session.rollback()
        return exc
    return None


@pytest.mark.asyncio
async def test_resolution_merges_across_sources(tmp_path: Path) -> None:
    adapter, _ = await _merged(tmp_path, "merge")
    nodes = _state(adapter)["nodes"]
    # one Pierre survives, carrying both sources' contribution
    assert "a_pierre" not in nodes or "b_pierre" not in nodes, "no cross-source merge"
    adapter.disconnect()


@pytest.mark.asyncio
async def test_delete_a_keeps_b_whole_and_cleans_a(tmp_path: Path) -> None:
    adapter, repo = await _merged(tmp_path, "delA")
    exc = _delete(adapter, repo, "a")
    st = _state(adapter)
    _dump("delA: after delete_source('a')", st)
    assert exc is None
    # B's entities and edges survive
    b_nodes = {"Pierre Bezúkhov", "Dolokhov"}
    assert b_nodes <= {n["label"] for n in st["nodes"].values()}, "B's entity destroyed"
    assert {"duels", "knows"} <= {e[1] for e in st["edges"].values()}, "B's edge destroyed"
    # nothing of A stranded
    for n in st["nodes"].values():
        assert "as source a tells it" not in str(n["props"]), f"A's text stranded on {n['label']}"
    assert all(e[3] != "a" and "per a" not in str(e) for e in st["edges"].values())
    adapter.disconnect()


@pytest.mark.asyncio
async def test_delete_b_keeps_a_whole_and_cleans_b(tmp_path: Path) -> None:
    adapter, repo = await _merged(tmp_path, "delB")
    exc = _delete(adapter, repo, "b")
    st = _state(adapter)
    _dump("delB: after delete_source('b')", st)
    assert exc is None
    labels = {n["label"] for n in st["nodes"].values()}
    assert "Dolokhov" not in labels, "B-only entity stranded"
    # A's Pierre and A's edge survive
    assert any("Pierre" in lab for lab in labels), "A's Pierre destroyed"
    assert "loves" in {e[1] for e in st["edges"].values()}, "A's edge destroyed"
    for n in st["nodes"].values():
        assert "as source b tells it" not in str(n["props"]), f"B's text stranded on {n['label']}"
        assert "Bezúkhov" not in str(n["props"].get("aliases")), "B's name stranded as alias"
    assert all(e[1] not in {"duels", "knows"} for e in st["edges"].values()), "B's edge stranded"
    adapter.disconnect()


@pytest.mark.asyncio
async def test_citations_follow_the_merge(tmp_path: Path) -> None:
    adapter, _ = await _merged(tmp_path, "cites")
    st = _state(adapter)
    dangling = [c for c in st["cites"] if c[0] not in st["nodes"]]
    dangling_r = [c for c in st["rcites"] if c[0] not in st["edges"]]
    assert not dangling, f"entity citations pointing at merged-away node ids: {dangling}"
    assert not dangling_r, f"relationship citations pointing at removed edge ids: {dangling_r}"
    adapter.disconnect()


def _plain(tmp_path: Path, name: str) -> tuple[SqliteAdapter, GraphRepository]:
    """The two sources, no resolution pass, plus two extra a-stamped nodes."""
    adapter, repo = _build(tmp_path, name)
    # PR #624's no-merge case: an uncited node (CCX import stamps source_id, no citation)
    repo.create_node(
        NodeCreate(template_id="character_a", label="Imported Entity", source_id="a"),
        custom_id="a_uncited",
    )
    # A CCX-imported node cited by both a and b, stamped "first link wins" with a.
    # The importer links every citing source (``assign_source_to_nodes``): a is its
    # home, b a member as well.
    repo.create_node(
        NodeCreate(template_id="character_a", label="Shared Entity", source_id="a"),
        custom_id="shared",
    )
    repo.assign_source_to_nodes(["shared"], "b", DB)
    for sid in ("a", "b"):
        adapter.create_citation(
            {
                "id": f"cite_shared_{sid}",
                "database_name": DB,
                "entity_uri": "shared",
                "entity_label": "Shared Entity",
                "source_id": sid,
                "chunk_id": f"chunk_{sid}",
                "extraction_method": "imported",
            }
        )
    adapter.session.commit()
    return adapter, repo


def test_no_merge_uncited_node_goes_with_its_source(tmp_path: Path) -> None:
    """PR #624's test_delete_collects_uncited_rows_that_the_fk_cascade_used_to_take, on main."""
    adapter, repo = _plain(tmp_path, "uncited")
    assert _delete(adapter, repo, "a") is None
    assert "a_uncited" not in _state(adapter)["nodes"]
    adapter.disconnect()


def test_no_merge_node_cited_by_two_sources_survives_deleting_one(tmp_path: Path) -> None:
    adapter, repo = _plain(tmp_path, "shared")
    assert _delete(adapter, repo, "a") is None
    st = _state(adapter)
    _dump("shared: after delete_source('a')", st)
    assert "shared" in st["nodes"], "b still cites it, yet the source_id FK cascade took it"
    assert st["nodes"]["shared"]["source_id"] == "b"  # re-homed to the member it still has
    adapter.disconnect()


# --- PR #624's cases, on main's design --------------------------------------------------


def _merged_pierre(st: dict[str, Any]) -> str:
    """The id of the one node A's and B's Pierre merged into."""
    ids = [nid for nid in ("a_pierre", "b_pierre") if nid in st["nodes"]]
    assert len(ids) == 1, f"expected one merged Pierre, got {ids}"
    return ids[0]


@pytest.mark.asyncio
async def test_delete_originating_source_keeps_a_node_the_other_source_attests(
    tmp_path: Path,
) -> None:
    """#624 test_delete_originating_source_keeps_a_node_the_other_source_attests."""
    adapter, repo = await _merged(tmp_path, "origin")
    pierre = _merged_pierre(_state(adapter))
    assert set(repo.node_members(pierre)) == {"a", "b"}
    assert _delete(adapter, repo, "a") is None
    st = _state(adapter)
    assert pierre in st["nodes"], "b still attests the merged node"
    assert st["nodes"][pierre]["source_id"] == "b"
    assert set(repo.node_members(pierre)) == {"b"}
    adapter.disconnect()


@pytest.mark.asyncio
async def test_delete_last_attesting_source_collects_the_node(tmp_path: Path) -> None:
    """#624 test_delete_last_attesting_source_collects_the_node (and the edge variant)."""
    adapter, repo = await _merged(tmp_path, "last")
    assert _delete(adapter, repo, "a") is None
    assert _delete(adapter, repo, "b") is None
    st = _state(adapter)
    assert st["nodes"] == {}
    assert st["edges"] == {}
    assert st["cites"] == []
    assert st["rcites"] == []
    s = adapter.session
    assert not s.exec(select(GraphNodeSource)).all()
    assert not s.exec(select(GraphEdgeSource)).all()
    assert not s.exec(select(GraphEdge).where(GraphEdge.source_id.is_(None))).all()  # type: ignore[union-attr]
    adapter.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["a", "b"])
async def test_delete_either_source_first_converges(tmp_path: Path, first: str) -> None:
    """#624 test_delete_either_source_first_converges."""
    adapter, repo = await _merged(tmp_path, f"order_{first}")
    second = "b" if first == "a" else "a"
    assert _delete(adapter, repo, first) is None
    mid = _state(adapter)
    assert mid["nodes"], f"deleting {first} first must leave the other source's graph"
    assert _delete(adapter, repo, second) is None
    assert _state(adapter)["nodes"] == {}
    adapter.disconnect()


# --- the other real entry points ----------------------------------------------------------


B_LABELS = {label for label, sid in NODES.values() if sid == "b"}


def _names_in(value: Any) -> set[str]:
    """Every string a node property holds, exactly (list members, dict keys and values, scalars)."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {n for k, v in value.items() for n in (_names_in(k) | _names_in(v))}
    if isinstance(value, (list, tuple, set)):
        return {n for v in value for n in _names_in(v)}
    return set()


def _b_names_gone(st: dict[str, Any]) -> None:
    """No surviving node carries any of B's labels in its label or any property.

    Covers aliases, merged names, attested names and ``resolved_from`` by exact match,
    so B's bare "Natasha" cannot stay on A's "Natasha Rostova" unseen.
    """
    for nid, n in st["nodes"].items():
        names = {n["label"], *_names_in(n["props"])}
        assert not names & B_LABELS, f"{nid}: B's names stranded: {sorted(names & B_LABELS)}"
        assert "as source b tells it" not in str(n["props"]), f"B's text on {n['label']}"
        assert "Bezúkhov" not in str(n["props"]), f"B's name in the properties of {nid}"
    assert all(e[1] not in {"duels", "knows"} and e[3] != "b" for e in st["edges"].values())


def _a_whole(st: dict[str, Any], *, remerged: bool = False) -> None:
    """A's nodes each keep their own reading, and A's edge survives.

    ``remerged``: B's content is back and merged in, so a node A's name also lives on may
    be labelled by B's reading; A's name must then still be one of its names, and a node
    whose label is A's keeps A's description.
    """
    by_label = {n["label"]: n for n in st["nodes"].values()}
    for label in ("Pierre", "Natasha Rostova"):
        if label in by_label:
            node = by_label[label]
            assert node["props"].get("description") == f"{label} as source a tells it"
        else:
            assert remerged, f"A's {label} is gone or renamed: {sorted(by_label)}"
            assert any(label in _names_in(n["props"]) for n in st["nodes"].values()), (
                f"A's {label} is on no surviving node"
            )
    assert "loves" in {e[1] for e in st["edges"].values()}, "A's edge destroyed"


@pytest.mark.asyncio
async def test_recommit_b_after_merge_keeps_a_and_remerges(tmp_path: Path) -> None:
    """Re-committing B with the same content: the real ``_cleanup_previous_commit`` releases B,
    the re-commit writes B's rows again (fresh ids, as a commit does), the real
    ``_refresh_released`` and post-commit resolution run.

    Driven: the real cleanup, refresh and resolution of ``SourceCommitService``. Not driven:
    the LLM extraction and ``_commit_impl`` as a whole; the fixture's ``_write_source_graph``
    stands in for what a commit writes, like the other cases here.
    """
    adapter, repo = await _merged(tmp_path, "recommit")
    pierre = _merged_pierre(_state(adapter))
    assert set(repo.node_members(pierre)) == {"a", "b"}

    svc = _commit_service(adapter, repo, source_repository=adapter)
    cleanup = svc._cleanup_previous_commit("b", "b")
    adapter.session.commit()
    assert cleanup["had_previous_data"] is True
    mid = _state(adapter)
    _dump("recommit: after cleanup of b", mid)
    _b_names_gone(mid)
    _a_whole(mid)
    assert pierre in mid["nodes"], "A's Pierre survives the release of B"
    assert set(repo.node_members(pierre)) == {"a"}
    await svc._refresh_released(cleanup["released"])

    # the re-commit: the same content, fresh ids
    _write_source_graph(adapter, repo, "b", prefix="r_")
    adapter.session.commit()
    new_b = {"r_" + n for n, (_, s) in NODES.items() if s == "b"}
    await _resolve(adapter, repo, scope=new_b)
    st = _state(adapter)
    _dump("recommit: after re-commit and resolution", st)

    # B's fresh Pierre merged into A's again; the resolver picks which id survives
    # (here the incoming node's), so assert on the one merged node, not on its id.
    pierres = [nid for nid, n in st["nodes"].items() if "Pierre" in n["label"]]
    assert len(pierres) == 1, f"B's Pierre did not merge back: {pierres}"
    assert set(repo.node_members(pierres[0])) == {"a", "b"}, "both sources attest the node again"
    assert {nid for nid in st["nodes"] if nid in {"a_pierre", "b_pierre"}} == set(), (
        "the pre-release node is gone, not duplicated beside the merged one"
    )
    assert {"Dolokhov"} <= {n["label"] for n in st["nodes"].values()}
    _a_whole(st, remerged=True)
    assert {"loves", "duels", "knows"} <= {e[1] for e in st["edges"].values()}
    live = set(st["nodes"])
    assert all(u in live for u, _ in st["cites"]), "an entity citation is dangling"
    assert all(u in set(st["edges"]) for u, _ in st["rcites"]), "a relationship citation dangles"
    adapter.disconnect()


@pytest.mark.asyncio
async def test_force_re_extract_b_keeps_a(tmp_path: Path) -> None:
    adapter, repo = await _merged(tmp_path, "reextract")
    pierre = _merged_pierre(_state(adapter))
    released = force_re_extract(
        source_id="b", database_name=DB, storage_adapter=adapter, settings=_settings()
    )
    adapter.session.commit()
    st = _state(adapter)
    _dump("reextract: after force_re_extract('b')", st)
    assert "b_dolokhov" in released.deleted_node_ids
    assert pierre in st["nodes"], "A's Pierre survives"
    _a_whole(st)
    _b_names_gone(st)
    assert set(repo.node_members(pierre)) == {"a"}
    assert st["nodes"][pierre]["label"] == "Pierre"
    assert st["nodes"][pierre]["props"]["description"] == "Pierre as source a tells it"
    # the caller's post-release refresh runs
    provider = SimpleNamespace(
        batch_embed=AsyncMock(
            side_effect=lambda texts: SimpleNamespace(embeddings=[[0.1, 0.2]] * len(texts))
        )
    )
    await refresh_after_release(
        adapter,
        DB,
        released,
        embedder=provider,
        search=None,  # type: ignore[arg-type]
    )
    adapter.session.commit()
    _b_names_gone(_state(adapter))
    adapter.disconnect()


@pytest.mark.asyncio
async def test_reset_processing_history_leaves_no_graph_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, _ = await _merged(tmp_path, "reset")
    db_path = tmp_path / "reset.db"
    monkeypatch.setattr(
        "chaoscypher_core.services.reset.data_reset.get_sqlite_adapter",
        lambda **_: adapter,
    )
    monkeypatch.setattr(
        "chaoscypher_core.services.reset.data_reset.get_settings",
        lambda: SimpleNamespace(
            paths=SimpleNamespace(
                data_dir=str(tmp_path), databases_subdir="dbs", imports_subdir="imports"
            )
        ),
    )
    stats = DataResetService(DB).reset_source_processing_history()
    assert stats["status"] == "success"
    check = SqliteAdapter(str(db_path), database_name=DB)
    check.connect()
    s = check.session
    for model in (GraphNode, GraphEdge, GraphTemplate, GraphNodeSource, GraphEdgeSource):
        assert not s.exec(select(model)).all(), f"{model.__name__} rows remain"
    check.disconnect()


# --- the real commit path: stable ids (#795) ---------------------------------------------
#
# A commit writes through ``EntityCommitHandler`` / ``upsert_nodes_batch``, whose ids are a
# hash of (database, source, template, label): B's re-commit computes the very ids its first
# commit wrote. B's node that was the kept side of a merge survives B's release under that
# id, re-homed to A, so B's re-commit lands on it ("first write wins"). The cases above write
# fresh prefixed ids and never reach that branch.

COMMITS: dict[str, dict[str, Any]] = {
    "a": {
        "entities": [
            {
                "name": "Pierre",
                "type": "character",
                "properties": {"description": "Pierre as source a tells it"},
            },
            {
                "name": "Natasha Rostova",
                "type": "character",
                "properties": {"description": "Natasha Rostova as source a tells it"},
            },
        ],
        "relationships": [{"source": 0, "target": 1, "type": "loves"}],
    },
    "b": {
        "entities": [
            {
                "name": "Pierre Bezúkhov",
                "type": "character",
                "properties": {"description": "Pierre Bezúkhov as source b tells it"},
            },
            {
                "name": "Dolokhov",
                "type": "character",
                "properties": {"description": "Dolokhov as source b tells it"},
            },
            {
                "name": "Natasha",
                "type": "character",
                "properties": {"description": "Natasha as source b tells it"},
            },
        ],
        "relationships": [
            {"source": 0, "target": 1, "type": "duels"},
            {"source": 0, "target": 2, "type": "knows"},
        ],
    },
}


def _stub_provider() -> SimpleNamespace:
    return SimpleNamespace(
        batch_embed=AsyncMock(
            side_effect=lambda texts: SimpleNamespace(embeddings=[[0.1, 0.2]] * len(texts))
        ),
        embed=AsyncMock(return_value=SimpleNamespace(embedding=[0.1, 0.2])),
    )


def _committer(adapter: SqliteAdapter) -> SourceCommitService:
    """The real commit service, every repository on ``adapter``'s session (Neuron's wiring)."""
    return SourceCommitService(
        graph_repository=GraphRepository(adapter.session, database_name=DB),
        source_repository=adapter,
        sources_repository=adapter,
        indexing_repository=adapter,
        search_repository=MagicMock(),
        settings=_settings(),
        embedding_provider=_stub_provider(),  # type: ignore[arg-type]
    )


async def _commit(adapter: SqliteAdapter, sid: str) -> None:
    await _committer(adapter).commit(
        sid, COMMITS[sid], {"id": sid, "database_name": DB, "filtering_mode": "unfiltered"}
    )
    adapter.session.commit()


async def _committed(tmp_path: Path, name: str) -> tuple[SqliteAdapter, GraphRepository, str]:
    """A then B committed through the real path; B's post-commit pass merges the Pierres.

    Returns the merged Pierre's id, which is B's own commit id (B's node is the kept side).
    """
    db_path = tmp_path / f"{name}.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name=DB)
    adapter.connect()
    for sid in ("a", "b"):
        adapter.create_source(
            {
                "id": sid,
                "database_name": DB,
                "filename": f"{sid}.txt",
                "filepath": str(tmp_path / f"{sid}.txt"),
                "file_type": "text",
                "file_size": 1,
                "content_hash": f"hash-{sid}",
                "status": "extracted",
            }
        )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE sources SET forced_domain = 'literary'")
    adapter.session.expire_all()
    await _commit(adapter, "a")
    await _commit(adapter, "b")
    repo = GraphRepository(adapter.session, database_name=DB)
    st = _state(adapter)
    _dump(f"{name}: A and B committed", st)
    pierres = [nid for nid, n in st["nodes"].items() if "Pierre" in n["label"]]
    assert len(pierres) == 1, f"the Pierres did not merge: {pierres}"
    pierre = pierres[0]
    assert st["nodes"][pierre]["source_id"] == "b", "B's node is the kept side"
    assert set(repo.node_members(pierre)) == {"a", "b"}
    return adapter, repo, pierre


def _ready_to_commit(adapter: SqliteAdapter, sid: str) -> None:
    """Put ``sid`` where a commit picks it up: extracted, its commit not complete."""
    row = adapter.session.get(SourceRow, sid)
    assert row is not None
    row.status = SourceStatus.EXTRACTED
    row.commit_complete = False
    adapter.session.add(row)
    adapter.session.commit()


def _b_whole(st: dict[str, Any]) -> None:
    """B's entities, descriptions and edges are all in the graph."""
    labels = {n["label"] for n in st["nodes"].values()}
    assert "Pierre Bezúkhov" in labels, f"B's Pierre destroyed: {sorted(labels)}"
    assert "Dolokhov" in labels, f"B's Dolokhov destroyed: {sorted(labels)}"
    descriptions = {str(n["props"].get("description")) for n in st["nodes"].values()}
    for label in ("Pierre Bezúkhov", "Dolokhov"):
        assert f"{label} as source b tells it" in descriptions, f"B's {label} description lost"
    assert {"duels", "knows"} <= {e[1] for e in st["edges"].values()}, "B's edges destroyed"


@pytest.mark.asyncio
async def test_real_recommit_b_then_delete_a_keeps_b_whole(tmp_path: Path) -> None:
    """Re-commit B through the real commit path, then delete A: B's content stays whole."""
    adapter, repo, pierre = await _committed(tmp_path, "real_recommit")
    _ready_to_commit(adapter, "b")  # a commit retry (``reset_for_retry`` to extracted)
    await _commit(adapter, "b")
    st = _state(adapter)
    _dump("real_recommit: after re-commit of b", st)
    assert pierre in st["nodes"], "B's re-commit lands on its own stable id"
    assert set(repo.node_members(pierre)) == {"a", "b"}, "the re-commit attests B's node again"
    assert _delete(adapter, repo, "a") is None
    st = _state(adapter)
    _dump("real_recommit: after delete_source('a')", st)
    _b_whole(st)
    assert set(repo.node_members(pierre)) == {"b"}
    assert st["nodes"][pierre]["label"] == "Pierre Bezúkhov"
    for n in st["nodes"].values():
        assert "as source a tells it" not in str(n["props"]), f"A's text stranded on {n['label']}"
    adapter.disconnect()


@pytest.mark.asyncio
async def test_real_force_re_extract_b_then_commit_then_delete_a_keeps_b_whole(
    tmp_path: Path,
) -> None:
    """Force re-extract B, commit it again through the real path, delete A: B stays whole."""
    adapter, repo, pierre = await _committed(tmp_path, "real_reextract")
    released = force_re_extract(
        source_id="b", database_name=DB, storage_adapter=adapter, settings=_settings()
    )
    adapter.session.commit()
    await refresh_after_release(adapter, DB, released, embedder=_stub_provider(), search=None)  # type: ignore[arg-type]
    adapter.session.commit()
    mid = _state(adapter)
    assert set(repo.node_members(pierre)) == {"a"}, "the release left A's reading only"
    assert mid["nodes"][pierre]["label"] == "Pierre"
    _ready_to_commit(adapter, "b")  # the new extraction finished
    await _commit(adapter, "b")
    st = _state(adapter)
    _dump("real_reextract: after the new commit of b", st)
    assert set(repo.node_members(pierre)) == {"a", "b"}, "the new commit attests B's node again"
    names = {st["nodes"][pierre]["label"], *_names_in(st["nodes"][pierre]["props"])}
    assert {"Pierre", "Pierre Bezúkhov"} <= names, "B's reading folds back into the node"
    assert _delete(adapter, repo, "a") is None
    st = _state(adapter)
    _dump("real_reextract: after delete_source('a')", st)
    _b_whole(st)
    assert set(repo.node_members(pierre)) == {"b"}
    adapter.disconnect()
