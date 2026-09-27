# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Exported packages must carry every template the exported knowledge references.

Extraction assigns nodes to domain-seeded *system* templates. The exporter
used to emit user templates only, so an extracted graph's node types named
templates the package did not contain and every entity imported as a
typeless "Imported Entity". Pin, against real SQLite adapters, that a
system template referenced by an exported node travels with the package and
survives an export → import round-trip.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import ccx
import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.models import (
    GraphEdge,
    GraphNode,
    GraphTemplate,
    SourceRow,
)
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings
from chaoscypher_core.services.export import CcxExporter
from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions


def _adapter(tmp_path: Path, name: str) -> SqliteAdapter:
    db_dir = tmp_path / name
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    SQLModel.metadata.create_all(get_engine(db_path), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    return adapter


@pytest.fixture
def source_adapter(tmp_path: Path) -> Iterator[SqliteAdapter]:
    adapter = _adapter(tmp_path, "source")
    yield adapter
    adapter.disconnect()
    evict_engine(tmp_path / "source" / "app.db")


@pytest.fixture
def dest_adapter(tmp_path: Path) -> Iterator[SqliteAdapter]:
    adapter = _adapter(tmp_path, "dest")
    yield adapter
    adapter.disconnect()
    evict_engine(tmp_path / "dest" / "app.db")


def _seed_extracted_graph(adapter: SqliteAdapter) -> None:
    """A system node template (as extraction seeds them), a user edge template,
    two typed nodes and one edge — the shape of any extracted graph.
    """
    assert adapter.session is not None
    session = adapter.session
    session.add(
        GraphTemplate(
            id="tpl_component",
            database_name="default",
            name="Component",
            template_type="node",
            is_system=True,
        )
    )
    session.add(
        GraphTemplate(
            id="tpl_depends",
            database_name="default",
            name="depends_on",
            template_type="edge",
            is_system=False,
        )
    )
    session.flush()
    # As extraction produces them: one generic system template, the specific
    # type on ``entity_type``.
    for node_id, label, entity_type in (
        ("node_cortex", "Cortex", "Service"),
        ("node_valkey", "Valkey", "Package"),
    ):
        session.add(
            GraphNode(
                id=node_id,
                database_name="default",
                graph_name="knowledge",
                template_id="tpl_component",
                entity_type=entity_type,
                label=label,
            )
        )
    session.flush()
    session.add(
        GraphEdge(
            id="edge_1",
            database_name="default",
            graph_name="knowledge",
            template_id="tpl_depends",
            source_node_id="node_cortex",
            target_node_id="node_valkey",
            label="depends_on",
        )
    )
    session.commit()


def _export(adapter: SqliteAdapter, **kwargs: object) -> bytes:
    assert adapter.session is not None
    exporter = CcxExporter(
        graph_repository=GraphRepository(adapter.session, "default"),
        sources_repository=adapter,
        settings=build_engine_settings(get_settings()),
    )
    return exporter.export(include_embeddings=False, **kwargs)  # type: ignore[arg-type]


def _template_names(data: bytes) -> set[tuple[str, str]]:
    pkg = ccx.open_package(data)
    for doc in pkg.graph_documents():
        if doc.namespace == "chaoscypher" and doc.name == "templates":
            return {
                (member.get("template_type") or "node", member["name"])
                for member in doc.doc.get("@graph", [])
            }
    return set()


def test_referenced_system_node_template_is_exported(source_adapter: SqliteAdapter) -> None:
    _seed_extracted_graph(source_adapter)

    names = _template_names(_export(source_adapter))

    assert ("node", "Component") in names
    assert ("edge", "depends_on") in names


def test_unreferenced_system_template_stays_out(source_adapter: SqliteAdapter) -> None:
    """The system-template filter still applies to templates nothing uses."""
    _seed_extracted_graph(source_adapter)
    assert source_adapter.session is not None
    source_adapter.session.add(
        GraphTemplate(
            id="tpl_unused",
            database_name="default",
            name="UnusedSystemType",
            template_type="node",
            is_system=True,
        )
    )
    source_adapter.session.commit()

    names = _template_names(_export(source_adapter))

    assert ("node", "Component") in names
    assert ("node", "UnusedSystemType") not in names


@pytest.mark.asyncio
async def test_round_trip_keeps_nodes_typed(
    source_adapter: SqliteAdapter, dest_adapter: SqliteAdapter
) -> None:
    _seed_extracted_graph(source_adapter)
    data = _export(source_adapter)

    assert dest_adapter.session is not None
    dest_repo = GraphRepository(dest_adapter.session, "default")
    importer = CcxImporter(graph_repository=dest_repo, sources_repository=dest_adapter)
    stats = await importer.import_from_bytes(data, ImportOptions(database_name="default"))

    assert stats.nodes_imported == 2, stats.errors
    template_names = {tmpl.id: tmpl.name for tmpl in dest_repo.list_templates()}
    nodes = {node.label: node for node in dest_repo.list_nodes(limit=10)}
    # Both survive: the (system) template binding AND the specific type.
    assert {label: template_names.get(n.template_id) for label, n in nodes.items()} == {
        "Cortex": "Component",
        "Valkey": "Component",
    }
    assert {label: n.entity_type for label, n in nodes.items()} == {
        "Cortex": "Service",
        "Valkey": "Package",
    }


def _seed_source_scoped_template(
    adapter: SqliteAdapter, *, source_id: str, enabled: bool, template_id: str, name: str
) -> None:
    """One source owning one user template, with one node typed by it."""
    assert adapter.session is not None
    session = adapter.session
    session.add(
        SourceRow(
            id=source_id,
            database_name="default",
            filename=f"{source_id}.md",
            filepath=f"/tmp/{source_id}.md",
            source_type="text",
            enabled=enabled,
        )
    )
    # Flush the source before the template: the FK is declared on
    # graph_templates.source_id, and SQLAlchemy does not order the two
    # inserts for us.
    session.flush()
    session.add(
        GraphTemplate(
            id=template_id,
            database_name="default",
            source_id=source_id,
            name=name,
            template_type="node",
            is_system=False,
        )
    )
    session.flush()
    session.add(
        GraphNode(
            id=f"node_{source_id}",
            database_name="default",
            graph_name="knowledge",
            template_id=template_id,
            entity_type=name,
            label=f"Entity from {source_id}",
            source_id=source_id,
        )
    )
    session.commit()


def test_disabled_source_template_still_exports(source_adapter: SqliteAdapter) -> None:
    """Disabling a source must not strip its schema from the package.

    ``list_templates()`` hides disabled sources' templates by default, but
    the exporter ships a disabled source's row and its nodes regardless —
    so the schema half went missing while the data half travelled, and the
    node's ``@type`` term arrived unbound.
    """
    _seed_source_scoped_template(
        source_adapter, source_id="src_off", enabled=False, template_id="tpl_off", name="Statute"
    )

    names = _template_names(_export(source_adapter))

    assert ("node", "Statute") in names


def test_disabled_source_template_binds_its_context_term(source_adapter: SqliteAdapter) -> None:
    """The ``include_templates and templates`` gate also controlled @context.

    When a disabled source owned the *only* user template, the whole
    templates branch short-circuited: no extended ``@context``, no
    ``shapes.ttl``, while the knowledge graph still emitted the ``@type``
    term that context was supposed to bind.
    """
    _seed_source_scoped_template(
        source_adapter, source_id="src_off", enabled=False, template_id="tpl_off", name="Statute"
    )

    pkg = ccx.open_package(_export(source_adapter))
    bound = False
    for doc in pkg.graph_documents():
        if doc.namespace == "ccx" and doc.name == "knowledge":
            bound = "Statute" in (doc.doc.get("@context") or {})
    assert bound, "@type term shipped without a binding in the package's own @context"


def test_enabled_source_template_unaffected(source_adapter: SqliteAdapter) -> None:
    """The change is additive — enabled sources behave exactly as before."""
    _seed_source_scoped_template(
        source_adapter, source_id="src_on", enabled=True, template_id="tpl_on", name="Contract"
    )

    names = _template_names(_export(source_adapter))

    assert ("node", "Contract") in names
