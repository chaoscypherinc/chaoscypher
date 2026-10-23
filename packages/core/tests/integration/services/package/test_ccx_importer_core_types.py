# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A bundle from before the core entity types imports onto them (clean break, 2026-10-08).

A database from before the core types is not migrated in place: it is
exported with the previous version, the product updated, the export
imported. These tests seed a v3-era graph (one template per old type:
``Character``, ``Literary Work``, ``Military Unit``, ``Social Group`` and a
type no table holds, a ``literary`` source with no pack list), export it
with ``CcxExporter`` (which writes ``@type`` = the old ``entity_type``,
exactly as the old exporter did), import it, and assert:

* every node is on its core type, the old name its ``entity_subtype``; the
  unknown one ``Concept`` flagged ``unplaced_type`` with ``written_type``;
* the old node templates are not created: each node's template is its core
  type's (one per core type, with the floor's icon);
* edges are untouched;
* the import's result carries the count table (old type -> core type, count);
* a bundle already on the core types (the import's own re-export) changes
  nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sqlmodel import SQLModel, select

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
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
from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions, ImportStats
from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack


if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


V3_NODES: list[tuple[str, str, str]] = [
    # (node id, label, old entity_type == its old template's name)
    ("n_pierre", "Pierre", "Character"),
    ("n_natasha", "Natásha", "Character"),
    ("n_war", "War and Peace", "Literary Work"),
    ("n_regiment", "the Pávlograd regiment", "Military Unit"),
    ("n_masons", "the Freemasons", "Social Group"),
    ("n_glow", "the glow", "Zorblat"),
]

EXPECTED: dict[str, tuple[str, dict[str, Any]]] = {
    "Pierre": ("Person", {"entity_subtype": "character"}),
    "Natásha": ("Person", {"entity_subtype": "character"}),
    "War and Peace": ("Work", {"entity_subtype": "literary_work"}),
    "the Pávlograd regiment": ("Organization", {"entity_subtype": "military_unit"}),
    "the Freemasons": ("Group", {"entity_subtype": "social_group"}),
    "the glow": ("Concept", {"unplaced_type": True, "written_type": "Zorblat"}),
}


_PER_IMPORT = frozenset({"source_document_id", "source_document_name"})
"""Node properties the import writes per import (the source's new local id)."""


def _seed_v3_graph(adapter: SqliteAdapter, database_name: str = "default") -> None:
    """A v3-era literary source: a template per old type, nodes typed by it, two edges, one chunk."""
    assert adapter.session is not None
    session = adapter.session
    session.add(
        SourceRow(
            id="src_wp",
            database_name=database_name,
            filename="war_and_peace.txt",
            filepath="/data/war_and_peace.txt",
            title="War and Peace, Book One",
            source_type="text",
            status="committed",
            extraction_domain="literary",
            full_text="Pierre met Natásha.",
        )
    )
    session.flush()
    for name in sorted({entity_type for _id, _label, entity_type in V3_NODES}):
        session.add(
            GraphTemplate(
                id=f"tpl_{name.lower().replace(' ', '_')}",
                database_name=database_name,
                name=name,
                template_type="node",
                icon="Person" if name == "Character" else None,
                source_id="src_wp",
            )
        )
    session.add(
        GraphTemplate(
            id="tpl_knows",
            database_name=database_name,
            name="knows",
            template_type="edge",
            source_id="src_wp",
        )
    )
    session.flush()
    for node_id, label, entity_type in V3_NODES:
        session.add(
            GraphNode(
                id=node_id,
                database_name=database_name,
                graph_name="knowledge",
                template_id=f"tpl_{entity_type.lower().replace(' ', '_')}",
                label=label,
                entity_type=entity_type,
                properties={"description": f"{label} in the novel"},
                source_id="src_wp",
            )
        )
    session.flush()
    for edge_id, source, target, props in (
        ("e_1", "n_pierre", "n_natasha", {"since": 1805}),
        ("e_2", "n_pierre", "n_masons", {"evidence": "joins the lodge"}),
    ):
        session.add(
            GraphEdge(
                id=edge_id,
                database_name=database_name,
                graph_name="knowledge",
                template_id="tpl_knows",
                source_node_id=source,
                target_node_id=target,
                label="knows",
                properties=props,
                source_id="src_wp",
            )
        )
    session.add(
        DocumentChunk(
            id="chunk_wp_0",
            database_name=database_name,
            source_id="src_wp",
            chunk_index=0,
            content="Pierre",
            char_start=0,
            char_end=6,
            status="committed",
        )
    )
    session.commit()


def _export(adapter: SqliteAdapter, database_name: str) -> bytes:
    assert adapter.session is not None
    exporter = CcxExporter(
        graph_repository=GraphRepository(adapter.session, database_name),
        sources_repository=adapter,
        settings=build_engine_settings(get_settings()),
        workflow_db=None,
    )
    return exporter.export(include_embeddings=False)


async def _import(adapter: SqliteAdapter, data: bytes, database_name: str) -> ImportStats:
    assert adapter.session is not None
    importer = CcxImporter(
        graph_repository=GraphRepository(adapter.session, database_name),
        sources_repository=adapter,
        workflow_db=None,
    )
    return await importer.import_from_bytes(data, ImportOptions(database_name=database_name))


def _graph(adapter: SqliteAdapter, database_name: str) -> dict[str, Any]:
    """Every node (label -> type, properties, template name), the node templates, the edges."""
    assert adapter.session is not None
    session = adapter.session
    templates = {
        t.id: t
        for t in session.exec(
            select(GraphTemplate).where(GraphTemplate.database_name == database_name)
        ).all()
    }
    nodes = session.exec(select(GraphNode).where(GraphNode.database_name == database_name)).all()
    edges = session.exec(select(GraphEdge).where(GraphEdge.database_name == database_name)).all()
    by_id = {n.id: n.label for n in nodes}
    return {
        "nodes": {
            n.label: (
                n.entity_type,
                # the import links a node to its (freshly minted) source id
                {k: v for k, v in (n.properties or {}).items() if k not in _PER_IMPORT},
                templates[n.template_id].name,
            )
            for n in nodes
        },
        "node_templates": sorted(t.name for t in templates.values() if t.template_type == "node"),
        "edges": sorted(
            (by_id[e.source_node_id], e.label, by_id[e.target_node_id], str(e.properties))
            for e in edges
        ),
    }


@pytest.mark.asyncio
async def test_an_old_shape_bundle_imports_onto_the_core_types(
    integration_adapter: SqliteAdapter,
) -> None:
    _seed_v3_graph(integration_adapter)
    before = _graph(integration_adapter, "default")

    stats = await _import(integration_adapter, _export(integration_adapter, "default"), "updated")
    assert not stats.errors, stats.errors
    after = _graph(integration_adapter, "updated")

    for label, (core, subtype_props) in EXPECTED.items():
        entity_type, properties, template = after["nodes"][label]
        assert entity_type == core, label
        assert properties == {"description": f"{label} in the novel", **subtype_props}, label
        assert template == core, label  # its core type's template, not the old one
    assert after["node_templates"] == ["Concept", "Group", "Organization", "Person", "Work"]

    # Edges are untouched: same endpoints, labels and properties.
    assert after["edges"] == before["edges"]

    # The core templates carry the floor's visuals (the commit's builder).
    floor = DomainStack.resolve(get_domain_registry(), []).get_templates()["node_templates"]
    floor_person = next(t for t in floor if t["name"] == "Person")
    assert integration_adapter.session is not None
    person = integration_adapter.session.exec(
        select(GraphTemplate).where(
            GraphTemplate.database_name == "updated", GraphTemplate.name == "Person"
        )
    ).one()
    assert (person.icon, person.color) == (floor_person["icon"], floor_person["color"])
    # ... and are the imported source's (they cascade-delete with it).
    assert person.source_id is not None

    # The result's count table: old type -> core type, count.
    assert stats.core_types_mapped == {
        "Character": {"core_type": "Person", "count": 2},
        "Literary Work": {"core_type": "Work", "count": 1},
        "Military Unit": {"core_type": "Organization", "count": 1},
        "Social Group": {"core_type": "Group", "count": 1},
        "Zorblat": {"core_type": "Concept", "count": 1, "unplaced": True},
    }
    assert stats.templates_repointed == {
        "Character": "Person",
        "Literary Work": "Work",
        "Military Unit": "Organization",
        "Social Group": "Group",
        "Zorblat": "Concept",
    }
    assert stats.to_dict()["core_types_mapped"] == stats.core_types_mapped

    # The imported source keeps its pack (the bundle's ``extractionDomain``).
    src = integration_adapter.get_source_by_ccx_iri("urn:ccx:chaoscypher:source/src_wp", "updated")
    assert src is not None
    assert src["extraction_packs"] == ["literary"]

    # Re-importing the same old bundle lands the same graph (idempotent).
    await _import(integration_adapter, _export(integration_adapter, "default"), "updated")
    assert _graph(integration_adapter, "updated") == after


@pytest.fixture
def fresh_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """A second, empty database file (chunk ids are global keys: one import per file)."""
    db_path = tmp_path / "fresh" / "app.db"
    db_path.parent.mkdir(parents=True)
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="again")
    adapter.connect()
    yield adapter
    adapter.disconnect()


@pytest.mark.asyncio
async def test_a_bundle_on_the_core_types_round_trips_unchanged(
    integration_adapter: SqliteAdapter, fresh_adapter: SqliteAdapter
) -> None:
    """The import's own re-export (core types, packs listed) maps nothing and lands the same graph."""
    _seed_v3_graph(integration_adapter)
    await _import(integration_adapter, _export(integration_adapter, "default"), "updated")
    updated = _graph(integration_adapter, "updated")

    stats = await _import(fresh_adapter, _export(integration_adapter, "updated"), "again")
    assert not stats.errors, stats.errors
    assert stats.core_types_mapped == {}
    assert stats.templates_repointed == {}
    assert _graph(fresh_adapter, "again") == updated

    src = fresh_adapter.get_source_by_ccx_iri("urn:ccx:chaoscypher:source/src_wp", "again")
    assert src is not None
    assert src["extraction_packs"] == ["literary"]
