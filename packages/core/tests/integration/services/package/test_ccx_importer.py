# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration test: CcxImporter round-trips a CcxExporter package.

Seeds a small graph (1 node template, 1 edge template, 2 nodes, 1 property
edge, 1 source with full_text + 2 chunks) through the real SQLite adapter,
exports it via ``CcxExporter``, then imports the produced bytes with
``CcxImporter`` into a SEPARATE database and asserts:

* nodes / edges / sources / templates land,
* edge properties survive (R2 lossless edge props),
* chunk offsets survive (full-text + selector round-trip),
* re-importing the SAME bytes twice leaves counts unchanged (idempotent
  upsert-by-IRI).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

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
from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _seed_small_graph(adapter: SqliteAdapter, database_name: str = "default") -> None:
    """Seed 1 node template, 1 edge template, 2 nodes, 1 property edge, 1 source + 2 chunks."""
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
        icon="Person",
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


def _export_seed_bytes(adapter: SqliteAdapter) -> bytes:
    """Seed and export a small graph, returning the .ccx package bytes."""
    _seed_small_graph(adapter)
    assert adapter.session is not None
    graph_repo = GraphRepository(adapter.session, "default")
    settings = build_engine_settings(get_settings())
    exporter = CcxExporter(
        graph_repository=graph_repo,
        sources_repository=adapter,
        settings=settings,
        workflow_db=None,
    )
    return exporter.export(include_embeddings=False)


def _counts(adapter: SqliteAdapter, database_name: str) -> dict[str, int]:
    """Return row counts for nodes / edges / sources / chunks / templates."""
    from sqlmodel import func, select

    assert adapter.session is not None
    session = adapter.session

    def _count(model: type) -> int:
        return int(
            session.exec(
                select(func.count()).select_from(model).where(model.database_name == database_name)
            ).one()
        )

    return {
        "nodes": _count(GraphNode),
        "edges": _count(GraphEdge),
        "sources": _count(SourceRow),
        "chunks": _count(DocumentChunk),
        "templates": _count(GraphTemplate),
    }


async def _import(
    target: SqliteAdapter,
    data: bytes,
    database_name: str = "imported",
) -> object:
    """Import package bytes into ``target`` under ``database_name``."""
    assert target.session is not None
    graph_repo = GraphRepository(target.session, database_name)
    importer = CcxImporter(
        graph_repository=graph_repo,
        sources_repository=target,
        workflow_db=None,
    )
    options = ImportOptions(database_name=database_name)
    return await importer.import_from_bytes(data, options)


@pytest.mark.asyncio
async def test_ccx_importer_round_trip_and_idempotent(
    integration_adapter: SqliteAdapter,
) -> None:
    """Export then import lands all content; a second import is a no-op."""
    data = _export_seed_bytes(integration_adapter)

    target_db = "imported"
    stats = await _import(integration_adapter, data, target_db)

    # The validate() report drives fail-closed behaviour; a clean import has
    # no errors and records the conformance classes.
    assert not stats.errors, stats.errors
    assert "core" in stats.conformance_classes
    assert "sources" in stats.conformance_classes

    # Everything landed under the target database.
    after_first = _counts(integration_adapter, target_db)
    assert after_first["templates"] >= 2  # Person (node) + WorksWith (edge)
    assert after_first["nodes"] == 2
    assert after_first["edges"] == 1
    assert after_first["sources"] == 1
    assert after_first["chunks"] == 2

    assert stats.nodes_imported == 2
    assert stats.edges_imported == 1
    assert stats.sources_imported == 1
    assert stats.chunks_imported == 2

    # Edge properties survived (R2 lossless edge props).
    assert integration_adapter.session is not None
    graph_repo = GraphRepository(integration_adapter.session, target_db)
    edges = graph_repo.list_edges(include_disabled_sources=True)
    assert len(edges) == 1
    assert edges[0].properties.get("since") == 2020
    assert edges[0].label == "worksWith"

    # The source landed with its full_text, and its chunks' content was
    # recovered by slicing full_text[char_start:char_end] (the source has
    # full_text, so even this full export takes the offset-selector path; the
    # offsets themselves are asserted by ``test_ccx_importer_full_text_offsets
    # _round_trip``).
    src = integration_adapter.get_source_by_ccx_iri("urn:ccx:chaoscypher:source/src_1", target_db)
    assert src is not None
    assert src["full_text"] == "Alice works with Bob."
    chunks = integration_adapter.list_chunks(
        database_name=target_db, source_id=src["id"], include_content=True
    )
    # full_text[0:5] == "Alice", full_text[17:20] == "Bob".
    assert {c["content"] for c in chunks} == {"Alice", "Bob"}

    # Re-import the SAME bytes twice — counts must be unchanged (upsert-by-IRI).
    await _import(integration_adapter, data, target_db)
    after_second = _counts(integration_adapter, target_db)
    assert after_second == after_first, (after_first, after_second)


def _seed_source_scoped(adapter: SqliteAdapter, database_name: str = "default") -> None:
    """Seed a source-scoped template + node + a source with full_text + 1 chunk.

    The template carries ``source_id`` so the ``source_ids``-scoped export
    keeps it (the exporter filters templates by source when scoped).
    """
    assert adapter.session is not None
    session = adapter.session

    session.add(
        SourceRow(
            id="src_ft",
            database_name=database_name,
            filename="ft.txt",
            filepath="/data/ft.txt",
            title="Full Text Doc",
            source_type="text",
            status="committed",
            full_text="Alice works with Bob.",
        )
    )
    session.flush()
    session.add(
        GraphTemplate(
            id="tpl_ft",
            database_name=database_name,
            name="Person",
            template_type="node",
            source_id="src_ft",
        )
    )
    session.flush()
    session.add(
        GraphNode(
            id="node_ft",
            database_name=database_name,
            graph_name="knowledge",
            template_id="tpl_ft",
            label="Alice",
            source_id="src_ft",
        )
    )
    session.add(
        DocumentChunk(
            id="chunk_ft",
            database_name=database_name,
            source_id="src_ft",
            chunk_index=0,
            content="Alice",
            char_start=0,
            char_end=5,
            status="committed",
        )
    )
    session.commit()


@pytest.mark.asyncio
async def test_ccx_importer_full_text_offsets_round_trip(
    integration_adapter: SqliteAdapter,
) -> None:
    """A source-scoped export carries full_text + offset selectors that import.

    Exercises the canonical CCX chunk model: the Source record carries a
    ``text`` asset and chunks carry ``TextPositionSelector`` offsets. The
    importer must recover ``full_text`` and slice the chunk content from it.
    """
    _seed_source_scoped(integration_adapter)
    assert integration_adapter.session is not None
    graph_repo = GraphRepository(integration_adapter.session, "default")
    settings = build_engine_settings(get_settings())
    exporter = CcxExporter(
        graph_repository=graph_repo,
        sources_repository=integration_adapter,
        settings=settings,
        workflow_db=None,
    )
    data = exporter.export(include_embeddings=False, source_ids=["src_ft"])

    target_db = "imported_ft"
    stats = await _import(integration_adapter, data, target_db)
    assert not stats.errors, stats.errors
    assert stats.sources_imported == 1
    assert stats.chunks_imported == 1

    src = integration_adapter.get_source_by_ccx_iri("urn:ccx:chaoscypher:source/src_ft", target_db)
    assert src is not None
    # full_text recovered from the text asset.
    assert src["full_text"] == "Alice works with Bob."

    listed = integration_adapter.list_chunks(
        database_name=target_db, source_id=src["id"], include_content=True
    )
    assert len(listed) == 1
    chunk = integration_adapter.get_chunk(listed[0]["id"], target_db)
    assert chunk is not None
    # Offsets survived AND content was sliced from full_text[start:end].
    assert chunk["char_start"] == 0
    assert chunk["char_end"] == 5
    assert chunk["content"] == "Alice"
