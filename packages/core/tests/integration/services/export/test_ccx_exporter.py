# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration test: CcxExporter produces a conformant CCX 3.0 package.

Seeds a small graph (2 nodes, 1 property edge, 1 source with full_text +
2 chunks, 1 node template) through the real SQLite adapter, exports it via
``CcxExporter(...).export(include_embeddings=False)``, then asserts the
produced bytes validate via ``ccx-format`` with the expected conformance
classes and that a ``ccx:Relationship`` appears in the default graph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ccx

from chaoscypher_core.adapters.sqlite.models import (
    DocumentChunk,
    GraphEdge,
    GraphNode,
    GraphTemplate,
    SourceRow,
)
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings
from chaoscypher_core.services.export import CcxExporter


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _seed_small_graph(adapter: SqliteAdapter, database_name: str = "default") -> None:
    """Seed 1 node template, 2 nodes, 1 property edge, 1 source + 2 chunks."""
    assert adapter.session is not None
    session = adapter.session

    source = SourceRow(
        id="src_1",
        database_name=database_name,
        filename="doc.txt",
        filepath="/data/doc.txt",
        title="Doc One",
        source_type="text",
        status="committed",
        full_text="Alice works with Bob.",
    )
    session.add(source)
    session.flush()

    node_tpl = GraphTemplate(
        id="tpl_person",
        database_name=database_name,
        name="Person",
        template_type="node",
        color="#ff0000",
    )
    edge_tpl = GraphTemplate(
        id="tpl_rel",
        database_name=database_name,
        name="WorksWith",
        template_type="edge",
        color=None,
    )
    session.add(node_tpl)
    session.add(edge_tpl)
    session.flush()

    session.add(
        GraphNode(
            id="node_alice",
            database_name=database_name,
            graph_name="knowledge",
            template_id="tpl_person",
            label="Alice",
            source_id="src_1",
        )
    )
    session.add(
        GraphNode(
            id="node_bob",
            database_name=database_name,
            graph_name="knowledge",
            template_id="tpl_person",
            label="Bob",
            source_id="src_1",
        )
    )
    session.flush()

    # A property-bearing edge -> reified ccx:Relationship in the default graph.
    session.add(
        GraphEdge(
            id="edge_1",
            database_name=database_name,
            graph_name="knowledge",
            template_id="tpl_rel",
            source_node_id="node_alice",
            target_node_id="node_bob",
            label="worksWith",
            properties={"since": 2020},
            source_id="src_1",
        )
    )

    session.add(
        DocumentChunk(
            id="chunk_0",
            database_name=database_name,
            source_id="src_1",
            chunk_index=0,
            content="Alice",
            char_start=0,
            char_end=5,
            status="committed",
        )
    )
    session.add(
        DocumentChunk(
            id="chunk_1",
            database_name=database_name,
            source_id="src_1",
            chunk_index=1,
            content="Bob",
            char_start=17,
            char_end=20,
            status="committed",
        )
    )
    session.commit()


def test_ccx_exporter_produces_conformant_package(
    integration_adapter: SqliteAdapter,
) -> None:
    """Export a seeded graph → bytes validate as CCX 3.0 with core+sources."""
    _seed_small_graph(integration_adapter)

    assert integration_adapter.session is not None
    graph_repo = GraphRepository(integration_adapter.session, "default")
    settings = build_engine_settings(get_settings())

    exporter = CcxExporter(
        graph_repository=graph_repo,
        sources_repository=integration_adapter,
        settings=settings,
        workflow_db=None,
    )

    data = exporter.export(include_embeddings=False)
    assert isinstance(data, bytes)

    pkg = ccx.open_package(data)
    report = pkg.validate()
    assert report.ok, report.errors
    assert "core" in report.classes
    assert "sources" in report.classes

    # A property edge round-trips as a ccx:Relationship in the default graph.
    know = next(g for g in pkg.graph_documents() if g.role == "default")
    assert any(obj.get("@type") == "ccx:Relationship" for obj in know.doc["@graph"]), know.doc[
        "@graph"
    ]

    # The source + its 2 chunks are present in sources.jsonl.
    sources = pkg.sources()
    assert any(rec.get("@type") == "ccx:Source" for rec in sources)
    assert sum(1 for rec in sources if rec.get("@type") == "ccx:Chunk") == 2


# A 1x1 transparent PNG (valid header + minimal IDAT) used as preview bytes.
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f9c0000000049454e44ae42"
    "6082"
)

_PREVIEW_ASSET_PATH = "assets/graph_preview.png"


def _exporter(adapter: SqliteAdapter) -> CcxExporter:
    assert adapter.session is not None
    graph_repo = GraphRepository(adapter.session, "default")
    settings = build_engine_settings(get_settings())
    return CcxExporter(
        graph_repository=graph_repo,
        sources_repository=adapter,
        settings=settings,
        workflow_db=None,
    )


def test_export_with_preview_png_bundles_asset(
    integration_adapter: SqliteAdapter,
) -> None:
    """preview_png bytes → assets/graph_preview.png present and package validates."""
    _seed_small_graph(integration_adapter)

    data = _exporter(integration_adapter).export(include_embeddings=False, preview_png=_PNG_BYTES)

    pkg = ccx.open_package(data)
    assert pkg.validate().ok, pkg.validate().errors

    asset_paths = {a.path for a in pkg.manifest.assets}
    assert _PREVIEW_ASSET_PATH in asset_paths
    assert pkg.asset_bytes(_PREVIEW_ASSET_PATH) == _PNG_BYTES


def test_export_without_preview_png_omits_asset(
    integration_adapter: SqliteAdapter,
) -> None:
    """No preview_png → no graph_preview asset (the omit path is preserved)."""
    _seed_small_graph(integration_adapter)

    data = _exporter(integration_adapter).export(include_embeddings=False)

    pkg = ccx.open_package(data)
    assert pkg.validate().ok, pkg.validate().errors

    asset_paths = {a.path for a in pkg.manifest.assets}
    assert _PREVIEW_ASSET_PATH not in asset_paths


def test_full_export_preserves_full_text_offset_selectors(
    integration_adapter: SqliteAdapter,
) -> None:
    """A FULL (unscoped) export of a full_text source uses offset selectors.

    Regression for the exporter's source reader: ``list_sources`` uses a
    narrow ``load_only`` projection that omits ``full_text``/``ccx_iri``, so a
    full export must re-fetch each source via ``get_source`` — otherwise it
    would silently drop the offset-selector / full-text-asset path that a
    source-scoped export keeps.
    """
    _seed_small_graph(integration_adapter)

    data = _exporter(integration_adapter).export(include_embeddings=False)

    pkg = ccx.open_package(data)
    assert pkg.validate().ok, pkg.validate().errors
    assert "sources" in pkg.validate().classes

    records = pkg.sources()
    source_rec = next(r for r in records if r.get("@type") == "ccx:Source")
    # The Source record carries a content-addressed text asset (full_text).
    assert "text" in source_rec
    assert pkg.container.has(source_rec["text"])

    chunk_recs = [r for r in records if r.get("@type") == "ccx:Chunk"]
    assert chunk_recs
    # Every chunk uses an offset selector (NOT inline content) since the
    # source has full_text and the chunks carry char offsets.
    for chunk in chunk_recs:
        assert chunk.get("selector", {}).get("type") == "TextPositionSelector"
        assert "content" not in chunk


# ---------------------------------------------------------------------------
# chaoscypher.statistics — all five stat types
# ---------------------------------------------------------------------------


def _statistics_members(pkg: ccx.CCXPackage) -> list[dict]:
    """Return the member objects of the ``chaoscypher.statistics`` named graph."""
    stats_graph = next(
        g for g in pkg.graph_documents() if g.namespace == "chaoscypher" and g.name == "statistics"
    )
    return stats_graph.doc["@graph"]


def _seed_lens_and_workflow_nodes(adapter: SqliteAdapter, database_name: str = "default") -> None:
    """Add system lens + workflow templates and one lens / workflow / step node.

    Layers on top of :func:`_seed_small_graph` so an export carries lens and
    workflow data alongside the knowledge + source data already seeded.
    """
    assert adapter.session is not None
    session = adapter.session

    session.add(
        GraphTemplate(
            id="system_lens",
            database_name=database_name,
            name="Lens",
            template_type="node",
            is_system=True,
        )
    )
    session.add(
        GraphTemplate(
            id="system_workflow",
            database_name=database_name,
            name="Workflow",
            template_type="node",
            is_system=True,
        )
    )
    session.add(
        GraphTemplate(
            id="system_workflow_step",
            database_name=database_name,
            name="WorkflowStep",
            template_type="node",
            is_system=True,
        )
    )
    session.flush()

    session.add(
        GraphNode(
            id="lens_1",
            database_name=database_name,
            graph_name="lenses",
            template_id="system_lens",
            label="My Lens",
            properties={
                "input_template": "tpl_person",
                "output_template": "tpl_person",
                "transformation_rules": [{"map": "label"}],
            },
        )
    )
    session.add(
        GraphNode(
            id="wf_1",
            database_name=database_name,
            graph_name="knowledge",
            template_id="system_workflow",
            label="My Workflow",
            properties={"enabled": True},
        )
    )
    session.add(
        GraphNode(
            id="wf_step_1",
            database_name=database_name,
            graph_name="knowledge",
            template_id="system_workflow_step",
            label="Step 1",
            properties={"tool": "core:query"},
        )
    )
    session.commit()


def test_statistics_graph_emits_all_five_stat_types(
    integration_adapter: SqliteAdapter,
) -> None:
    """A full export carries Knowledge/Template/Source/Lens/Workflow stats.

    Each is a typed member of the ``chaoscypher.statistics`` named graph keyed
    by ``@type`` with the DTO fields flat on the member (the fixed hub
    convention).
    """
    _seed_small_graph(integration_adapter)
    _seed_lens_and_workflow_nodes(integration_adapter)

    assert integration_adapter.session is not None
    graph_repo = GraphRepository(integration_adapter.session, "default")
    settings = build_engine_settings(get_settings())
    exporter = CcxExporter(
        graph_repository=graph_repo,
        sources_repository=integration_adapter,
        settings=settings,
        # The adapter exposes list_triggers; with no Workflow/Trigger rows it
        # returns [], so WorkflowStats here is driven by the workflow NODES.
        workflow_db=integration_adapter,
    )

    data = exporter.export(include_embeddings=False)
    pkg = ccx.open_package(data)
    assert pkg.validate().ok, pkg.validate().errors

    members = _statistics_members(pkg)
    by_type = {m["@type"]: m for m in members}
    assert set(by_type) == {
        "chaoscypher:KnowledgeStats",
        "chaoscypher:TemplateStats",
        "chaoscypher:SourceStats",
        "chaoscypher:LensStats",
        "chaoscypher:WorkflowStats",
    }

    # Flat DTO fields land directly on the typed member (no nesting under a
    # sub-key) and reflect the seeded data.
    knowledge = by_type["chaoscypher:KnowledgeStats"]
    assert knowledge["node_count"] == 2  # lens/workflow nodes excluded
    assert knowledge["edge_count"] == 1

    source = by_type["chaoscypher:SourceStats"]
    assert source["total_chunks"] == 2
    assert source["source_types"] == {"text": 1}

    lens = by_type["chaoscypher:LensStats"]
    assert lens["total_count"] == 1
    assert lens["input_templates"] == {"tpl_person": 1}
    assert lens["has_transformation_rules"] == 1

    workflow = by_type["chaoscypher:WorkflowStats"]
    assert workflow["total_workflows"] == 1
    assert workflow["total_steps"] == 1
    assert workflow["tools_used"] == {"core:query": 1}


def test_statistics_graph_knowledge_only_omits_extra_stats(
    integration_adapter: SqliteAdapter,
) -> None:
    """A knowledge-only export emits ONLY Knowledge + Template stats.

    No sources / lenses / workflows in scope → no empty Source/Lens/Workflow
    members are written.
    """
    _seed_small_graph(integration_adapter)

    assert integration_adapter.session is not None
    graph_repo = GraphRepository(integration_adapter.session, "default")
    settings = build_engine_settings(get_settings())
    exporter = CcxExporter(
        graph_repository=graph_repo,
        sources_repository=integration_adapter,
        settings=settings,
        workflow_db=None,
    )

    data = exporter.export(
        include_embeddings=False,
        include_sources=False,
        include_lenses=False,
        include_workflows=False,
    )
    pkg = ccx.open_package(data)
    assert pkg.validate().ok, pkg.validate().errors

    types = {m["@type"] for m in _statistics_members(pkg)}
    assert types == {"chaoscypher:KnowledgeStats", "chaoscypher:TemplateStats"}
