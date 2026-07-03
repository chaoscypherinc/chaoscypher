# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Generate the ``seed.ccx`` fixture for E2E tests (CCX 3.0).

Run: ``uv run python e2e/fixtures/generate_seed.py``

Builds the seed the same way a real export is produced: seed a small graph
through the SQLite adapter, then export it with ``CcxExporter``. This keeps the
fixture structurally identical to packages users actually produce and lets it
self-validate via ``ccx-format`` (``ccx.open_package(...).validate().ok``).

The seed exercises every content type the round-trip test relies on:

* 2 node templates (Person, Organization) + 1 edge template (Works At).
* nodes: 2 people + 1 organization.
* a **property-bearing edge** (``works at`` with ``{"since": 2023}``) → a
  reified ``ccx:Relationship`` in the knowledge graph.
* a **simple edge** (``collaborates with``, no properties) → a plain triple.
* a **source with ``full_text`` + 2 chunks** carrying ``char_start`` /
  ``char_end`` offsets → exercises the offset-selector / full-text-asset path.
* a **lens node** (``system_lens``) → the ``chaoscypher.lenses`` named graph.

The result is written to ``e2e/fixtures/seed.ccx`` and validated before exit.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


# A lens node carries this template id so the exporter routes it to the
# chaoscypher.lenses named graph (mirrors CcxExporter._LENS_TEMPLATE_ID).
_LENS_TEMPLATE_ID = "system_lens"

# The full text the source's two chunks index into (offsets reference this).
_FULL_TEXT = "Alice Smith works at Acme Corporation. Bob Jones collaborates with Alice."


def _seed_graph(adapter) -> None:
    """Seed templates, nodes, edges, a full-text source + chunks, and a lens."""
    from chaoscypher_core.adapters.sqlite.models import (
        DocumentChunk,
        GraphEdge,
        GraphNode,
        GraphTemplate,
        SourceRow,
    )

    assert adapter.session is not None, "adapter must be connected"
    session = adapter.session
    db = "default"

    # --- Source with full_text (offset selectors + full-text asset) ---
    session.add(
        SourceRow(
            id="e2e_src_doc",
            database_name=db,
            filename="seed_doc.txt",
            filepath="/data/seed_doc.txt",
            title="E2E Seed Document",
            source_type="text",
            status="committed",
            full_text=_FULL_TEXT,
        )
    )
    session.flush()

    # --- Templates: 2 node, 1 edge ---
    session.add(
        GraphTemplate(
            id="e2e_person",
            database_name=db,
            name="Person",
            template_type="node",
            color="#4A90D9",
            icon="user",
        )
    )
    session.add(
        GraphTemplate(
            id="e2e_organization",
            database_name=db,
            name="Organization",
            template_type="node",
            color="#7B68EE",
            icon="building",
        )
    )
    session.add(
        GraphTemplate(
            id="e2e_works_at",
            database_name=db,
            name="Works At",
            template_type="edge",
            color="#2ECC71",
        )
    )
    session.flush()

    # --- Nodes: 2 people + 1 organization ---
    session.add(
        GraphNode(
            id="e2e_node_alice",
            database_name=db,
            graph_name="knowledge",
            template_id="e2e_person",
            label="Alice Smith",
            properties={"role": "Engineer"},
            source_id="e2e_src_doc",
        )
    )
    session.add(
        GraphNode(
            id="e2e_node_bob",
            database_name=db,
            graph_name="knowledge",
            template_id="e2e_person",
            label="Bob Jones",
            properties={"role": "Designer"},
            source_id="e2e_src_doc",
        )
    )
    session.add(
        GraphNode(
            id="e2e_node_acme",
            database_name=db,
            graph_name="knowledge",
            template_id="e2e_organization",
            label="Acme Corporation",
            properties={"industry": "Technology"},
            source_id="e2e_src_doc",
        )
    )
    session.flush()

    # --- A property-bearing edge → reified ccx:Relationship ---
    session.add(
        GraphEdge(
            id="e2e_edge_alice_acme",
            database_name=db,
            graph_name="knowledge",
            template_id="e2e_works_at",
            source_node_id="e2e_node_alice",
            target_node_id="e2e_node_acme",
            label="works at",
            properties={"since": 2023},
            source_id="e2e_src_doc",
        )
    )
    # --- A simple edge (no properties) → plain triple ---
    session.add(
        GraphEdge(
            id="e2e_edge_bob_alice",
            database_name=db,
            graph_name="knowledge",
            template_id="e2e_works_at",
            source_node_id="e2e_node_bob",
            target_node_id="e2e_node_alice",
            label="collaborates with",
            properties={},
            source_id="e2e_src_doc",
        )
    )

    # --- Two chunks with offsets into _FULL_TEXT ---
    session.add(
        DocumentChunk(
            id="e2e_chunk_0",
            database_name=db,
            source_id="e2e_src_doc",
            chunk_index=0,
            content=_FULL_TEXT[0:38],
            char_start=0,
            char_end=38,
            status="committed",
        )
    )
    session.add(
        DocumentChunk(
            id="e2e_chunk_1",
            database_name=db,
            source_id="e2e_src_doc",
            chunk_index=1,
            content=_FULL_TEXT[39:73],
            char_start=39,
            char_end=73,
            status="committed",
        )
    )

    # --- A lens node → chaoscypher.lenses named graph ---
    session.add(
        GraphTemplate(
            id=_LENS_TEMPLATE_ID,
            database_name=db,
            name="Lens",
            template_type="node",
            color=None,
        )
    )
    session.flush()
    session.add(
        GraphNode(
            id="e2e_node_lens",
            database_name=db,
            graph_name="knowledge",
            template_id=_LENS_TEMPLATE_ID,
            label="Engineering Lens",
            properties={"description": "People + orgs"},
        )
    )

    session.commit()


def main() -> None:
    """Seed a graph, export it as CCX 3.0, write + validate ``seed.ccx``."""
    output_path = Path(__file__).resolve().parent / "seed.ccx"

    # Point the engine at a throwaway data dir so the script never touches a
    # developer's real config/database. ``ignore_cleanup_errors`` because the
    # pooled SQLite engine can keep the .db file briefly open on Windows.
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    os.environ["CHAOSCYPHER_DATA_DIR"] = tmp.name

    from sqlmodel import SQLModel

    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.engine import get_engine
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.app_config.engine_factory import build_engine_settings
    from chaoscypher_core.services.export import CcxExporter

    db_path = str(Path(tmp.name) / "seed.db")
    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(db_path, database_name="default")
    adapter.connect()
    try:
        _seed_graph(adapter)

        assert adapter.session is not None
        graph_repo = GraphRepository(adapter.session, "default")
        settings = build_engine_settings(get_settings())
        exporter = CcxExporter(
            graph_repository=graph_repo,
            sources_repository=adapter,
            settings=settings,
            workflow_db=None,
        )
        data = exporter.export(
            include_templates=True,
            include_knowledge=True,
            include_lenses=True,
            include_workflows=False,  # no workflow_db in this standalone script
            include_sources=True,
            include_embeddings=False,
            title="E2E seed fixture",
        )
    finally:
        adapter.disconnect()
        engine.dispose()
        tmp.cleanup()

    output_path.write_bytes(data)

    # Validate the produced fixture before exiting.
    import ccx

    report = ccx.open_package(data).validate()
    if not report.ok:
        raise SystemExit(f"seed.ccx failed CCX validation: {report.errors}")

    print(f"Generated {output_path} ({len(data)} bytes)")
    print(f"  validate().ok = {report.ok}")
    print(f"  classes = {report.classes}")
    if report.warnings:
        print(f"  warnings = {report.warnings}")


if __name__ == "__main__":
    main()
