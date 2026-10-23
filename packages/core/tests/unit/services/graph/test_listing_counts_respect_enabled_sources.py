# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Listing pagination totals must exclude disabled-source rows, matching rows shown.

The entities / relationships / templates pages render rows filtered to enabled
sources (``list_*`` defaults ``include_disabled_sources=False``) but historically
derived their pagination total from ``count_*`` queries that ignored source
enabled-state. Disabling a source then left an inflated total and phantom
trailing pages -- and toggling a source never moved the displayed count.

These tests pin the invariant: a single-page listing's ``pagination.total``
equals the number of rows it actually returns once a source is disabled.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.adapters.sqlite.repos.graph_breakdown import GraphBreakdownQueryRepository
from chaoscypher_core.models import EdgeCreate, NodeCreate, TemplateCreate
from chaoscypher_core.ports.search import item_in_source_scope, item_visible
from chaoscypher_core.services.graph.management.edge import EdgeService
from chaoscypher_core.services.graph.management.node import NodeService
from chaoscypher_core.services.graph.management.template import TemplateService
from chaoscypher_core.services.graph.snapshot.build_service import BuildGraphSnapshotService
from chaoscypher_core.settings import EngineSettings


DB_NAME = "default"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Isolated file-backed SqliteAdapter with all tables created."""
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    a = SqliteAdapter(str(db_path), database_name=DB_NAME)
    a.connect()
    yield a
    a.disconnect()


@pytest.fixture
def repo(adapter: SqliteAdapter) -> GraphRepository:
    """GraphRepository sharing the adapter's session."""
    return GraphRepository(session=adapter.session, database_name=DB_NAME)


def _make_source(adapter: SqliteAdapter, source_id: str, *, enabled: bool) -> None:
    """Seed a source row in the given enabled state."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": DB_NAME,
            "filename": f"{source_id}.txt",
            "filepath": f"/tmp/{source_id}.txt",
            "file_type": "text",
            "file_size": 1,
            "content_hash": source_id,
            "status": "indexed",
            "enabled": enabled,
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_node_listing_total_excludes_disabled_source_rows(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Entities page: pagination total counts only enabled-source nodes."""
    _make_source(adapter, "src-on", enabled=True)
    _make_source(adapter, "src-off", enabled=False)

    tpl = repo.create_template(
        TemplateCreate(name="Person", template_type="node", properties=[]),
        is_system=True,
    )
    for i in range(3):
        repo.create_node(NodeCreate(template_id=tpl.id, label=f"on-{i}", source_id="src-on"))
    for i in range(2):
        repo.create_node(NodeCreate(template_id=tpl.id, label=f"off-{i}", source_id="src-off"))

    service = NodeService.from_adapter(adapter, EngineSettings(current_database=DB_NAME))
    result = service.list_nodes(page=1, page_size=50)

    assert len(result["data"]) == 3, "only enabled-source nodes are rendered"
    assert result["pagination"]["total"] == 3, "total must exclude disabled-source nodes"
    assert result["pagination"]["total"] == len(result["data"])


def test_edge_listing_total_excludes_disabled_source_rows(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Relationships page: pagination total counts only enabled-source edges."""
    _make_source(adapter, "src-on", enabled=True)
    _make_source(adapter, "src-off", enabled=False)

    node_tpl = repo.create_template(
        TemplateCreate(name="Person", template_type="node", properties=[]),
        is_system=True,
    )
    edge_tpl = repo.create_template(
        TemplateCreate(name="knows", template_type="edge", properties=[]),
        is_system=True,
    )
    nodes = [
        repo.create_node(NodeCreate(template_id=node_tpl.id, label=f"n{i}", source_id="src-on"))
        for i in range(4)
    ]
    # Distinct endpoint pairs so edge-dedup (same endpoints + template) does not
    # collapse these into a single row.
    repo.create_edge(
        EdgeCreate(
            template_id=edge_tpl.id,
            source_node_id=nodes[0].id,
            target_node_id=nodes[1].id,
            label="e1",
            source_id="src-on",
        )
    )
    repo.create_edge(
        EdgeCreate(
            template_id=edge_tpl.id,
            source_node_id=nodes[1].id,
            target_node_id=nodes[2].id,
            label="e2",
            source_id="src-on",
        )
    )
    repo.create_edge(
        EdgeCreate(
            template_id=edge_tpl.id,
            source_node_id=nodes[2].id,
            target_node_id=nodes[3].id,
            label="e3",
            source_id="src-off",
        )
    )

    service = EdgeService(repo)
    result = service.list_edges(page=1, page_size=50)

    assert len(result["data"]) == 2, "only enabled-source edges are rendered"
    assert result["pagination"]["total"] == 2, "total must exclude disabled-source edges"
    assert result["pagination"]["total"] == len(result["data"])


def test_template_listing_total_excludes_disabled_source_rows(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Templates page: total counts enabled-source + NULL-source templates only.

    Templates with a NULL source_id (system / manually created) are always
    visible; a template tied to a disabled source must be excluded from both
    the rendered rows and the total.
    """
    _make_source(adapter, "src-on", enabled=True)
    _make_source(adapter, "src-off", enabled=False)

    repo.create_template(
        TemplateCreate(name="A", template_type="node", properties=[], source_id="src-on")
    )
    repo.create_template(
        TemplateCreate(name="B", template_type="node", properties=[], source_id="src-off")
    )
    repo.create_template(
        TemplateCreate(name="Sys", template_type="node", properties=[]),
        is_system=True,
    )

    service = TemplateService(repo)
    result = service.list_templates(page=1, page_size=50)

    assert len(result["data"]) == 2, "enabled-source + NULL-source templates are rendered"
    assert result["pagination"]["total"] == 2, "total must exclude disabled-source templates"
    assert result["pagination"]["total"] == len(result["data"])


# ---------------------------------------------------------------------------
# Merged rows read membership (#795, D7): visible while any member source is
# enabled, counted once under each member.
# ---------------------------------------------------------------------------


def _merged_graph(adapter: SqliteAdapter, repo: GraphRepository) -> dict[str, str]:
    """A node and an edge homed at ``src-b`` that ``src-a`` also attests, and one node of each.

    ``src-a`` is enabled; ``src-b`` is switched off. The merged rows are
    homed at the disabled source, so only their membership keeps them in
    sight.
    """
    _make_source(adapter, "src-a", enabled=True)
    _make_source(adapter, "src-b", enabled=False)
    node_tpl = repo.create_template(
        TemplateCreate(name="Person", template_type="node", properties=[]), is_system=True
    )
    edge_tpl = repo.create_template(
        TemplateCreate(name="knows", template_type="edge", properties=[]), is_system=True
    )
    merged = repo.create_node(NodeCreate(template_id=node_tpl.id, label="Ada", source_id="src-b"))
    only_a = repo.create_node(NodeCreate(template_id=node_tpl.id, label="Bob", source_id="src-a"))
    only_b = repo.create_node(NodeCreate(template_id=node_tpl.id, label="Cy", source_id="src-b"))
    edge = repo.create_edge(
        EdgeCreate(
            template_id=edge_tpl.id,
            source_node_id=merged.id,
            target_node_id=only_a.id,
            label="knows",
            source_id="src-b",
        )
    )
    repo.record_node_sources([(merged.id, "src-a", {"label": "Ada"})])
    repo.record_edge_sources([(edge.id, "src-a", {"label": "knows"})])
    adapter.session.commit()
    return {
        "merged": merged.id,
        "only_a": only_a.id,
        "only_b": only_b.id,
        "edge": edge.id,
        "template": node_tpl.id,
    }


def _set_enabled(adapter: SqliteAdapter, source_id: str, *, enabled: bool) -> None:
    adapter.session.exec(  # type: ignore[call-overload]
        text("UPDATE sources SET enabled = :on WHERE id = :sid").bindparams(
            on=int(enabled), sid=source_id
        )
    )
    adapter.session.commit()


def test_merged_node_visible_while_any_member_enabled(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Review Focus 5: a node merged from an enabled and a disabled source stays visible.

    Every enabled-source reader agrees: the listings, their counts, the
    minimal loads the analytics read and the search's scope. Disabling the
    second member hides the node too.
    """
    ids = _merged_graph(adapter, repo)
    nodes = NodeService.from_adapter(adapter, EngineSettings(current_database=DB_NAME))
    edges = EdgeService(repo)

    listed = nodes.list_nodes(page=1, page_size=50)
    assert {n["id"] for n in listed["data"]} == {ids["merged"], ids["only_a"]}
    assert listed["pagination"]["total"] == 2
    assert {n.id for n in repo.list_nodes_minimal()} == {ids["merged"], ids["only_a"]}
    assert repo.count_nodes(include_disabled_sources=False) == 2
    assert repo.count_nodes_by_template([ids["template"]], include_disabled_sources=False) == 2
    listed_edges = edges.list_edges(page=1, page_size=50)
    assert [e["id"] for e in listed_edges["data"]] == [ids["edge"]]
    assert listed_edges["pagination"]["total"] == 1
    assert [e.id for e in repo.list_edges_minimal()] == [ids["edge"]]
    by_node = repo.list_edges_by_node([ids["merged"]], outgoing=True, limit_per_node=5)
    assert [e.id for e in by_node[ids["merged"]]] == [ids["edge"]]

    _set_enabled(adapter, "src-a", enabled=False)

    assert nodes.list_nodes(page=1, page_size=50)["pagination"]["total"] == 0
    assert repo.list_nodes_minimal() == []
    assert repo.count_nodes(include_disabled_sources=False) == 0
    assert edges.list_edges(page=1, page_size=50)["data"] == []
    assert repo.list_edges_minimal() == []
    assert repo.count_edges(include_disabled_sources=False) == 0


def test_merged_node_in_scope_of_every_member(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """A source scope holds every row the source attests, not only the rows homed at it."""
    ids = _merged_graph(adapter, repo)

    scoped = repo.list_nodes(source_ids=["src-a"], include_disabled_sources=True)
    assert {n.id for n in scoped} == {ids["merged"], ids["only_a"]}
    assert repo.count_nodes_by_source(["src-a"]) == 2
    minimal = repo.list_nodes_minimal(source_ids=["src-a"], include_disabled_sources=True)
    assert {n.id for n in minimal} == {ids["merged"], ids["only_a"]}
    assert [e.id for e in repo.list_edges(source_ids=["src-a"], include_disabled_sources=True)] == [
        ids["edge"]
    ]
    assert repo.count_edges(source_ids=["src-a"]) == 1
    exported = repo.export_graph_records(source_ids=["src-a"])
    assert {n["id"] for n in exported["nodes"]} == {ids["merged"], ids["only_a"]}
    assert [e["id"] for e in exported["edges"]] == [ids["edge"]]


def test_merged_node_counts_under_each_member(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """D7: the per-source counts count a merged node once under each member, the total once."""
    ids = _merged_graph(adapter, repo)
    breakdown = GraphBreakdownQueryRepository(adapter.session)
    sources = ["src-a", "src-b"]

    assert breakdown.count_nodes_per_source(DB_NAME, sources) == {"src-a": 2, "src-b": 2}
    assert breakdown.count_template_entities_per_source(DB_NAME, sources) == {
        "src-a": {ids["template"]: 2},
        "src-b": {ids["template"]: 2},
    }
    assert breakdown.count_nodes(DB_NAME, sources) == 3
    assert breakdown.count_nodes(DB_NAME, ["src-a"]) == 2
    # The merged node and Bob are both src-a's: their edge is internal to src-a.
    assert breakdown.count_internal_links_per_source(DB_NAME, sources) == {"src-a": 1}
    assert breakdown.count_edges(DB_NAME, ["src-a"]) == 1
    assert breakdown.count_source_attributed_nodes(DB_NAME) == 3

    snapshot = BuildGraphSnapshotService(breakdown).build(DB_NAME)
    assert snapshot.stats.total_nodes == 3, "a merged node is one node in the total"
    assert {s.id: s.total_entities for s in snapshot.sources} == {"src-a": 2, "src-b": 2}
    assert snapshot.stats.total_nodes == breakdown.count_source_attributed_nodes(DB_NAME)


def test_unsourced_row_keeps_its_null_reading(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """D7: a row with no source and no membership is visible, in every minimal scope, in no count by source."""
    ids = _merged_graph(adapter, repo)
    tpl = ids["template"]
    legacy = repo.create_node(NodeCreate(template_id=tpl, label="Legacy"))
    adapter.session.commit()

    assert legacy.id in {n.id for n in repo.list_nodes()}
    assert legacy.id in {n.id for n in repo.list_nodes_minimal(source_ids=["src-a"])}
    assert legacy.id not in {n.id for n in repo.list_nodes(source_ids=["src-a"])}
    assert repo.count_nodes_by_source(["src-a"]) == 2
    breakdown = GraphBreakdownQueryRepository(adapter.session)
    assert breakdown.count_source_attributed_nodes(DB_NAME) == 3
    assert breakdown.count_nodes_per_source(DB_NAME, ["src-a", "src-b"]) == {
        "src-a": 2,
        "src-b": 2,
    }


def test_loaded_nodes_carry_their_members(adapter: SqliteAdapter, repo: GraphRepository) -> None:
    """The in-memory scope filters read the same membership the SQL does."""
    ids = _merged_graph(adapter, repo)

    loads = {
        "get_node": [repo.get_node(ids["merged"])],
        "get_nodes_batch": repo.get_nodes_batch([ids["merged"]], include_embedding=False),
        "list_nodes": [n for n in repo.list_nodes() if n.id == ids["merged"]],
        "list_nodes_minimal": [n for n in repo.list_nodes_minimal() if n.id == ids["merged"]],
    }
    for name, (node,) in loads.items():
        assert sorted(node.member_source_ids) == ["src-a", "src-b"], name
        assert item_in_source_scope(node, ["src-a"]), name
        assert item_visible(node, {"src-b"}), name
        assert not item_visible(node, {"src-a", "src-b"}), name
    assert "member_source_ids" not in loads["get_node"][0].model_dump()


def test_template_listed_while_a_visible_node_uses_it(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """A template homed at a disabled source is listed while a node an enabled source attests uses it."""
    _make_source(adapter, "src-a", enabled=False)
    _make_source(adapter, "src-b", enabled=True)
    tpl = repo.create_template(
        TemplateCreate(name="Person", template_type="node", properties=[], source_id="src-a")
    )
    unused = repo.create_template(
        TemplateCreate(name="Place", template_type="node", properties=[], source_id="src-a")
    )
    merged = repo.create_node(NodeCreate(template_id=tpl.id, label="Ada", source_id="src-a"))
    repo.record_node_sources([(merged.id, "src-b", {"label": "Ada"})])
    adapter.session.commit()
    templates = TemplateService(repo)

    listed = templates.list_templates(page=1, page_size=50)
    assert [t["id"] for t in listed["data"]] == [tpl.id]
    assert listed["pagination"]["total"] == 1
    assert unused.id not in {t.id for t in repo.list_templates()}

    _set_enabled(adapter, "src-b", enabled=False)

    assert templates.list_templates(page=1, page_size=50)["pagination"]["total"] == 0
