# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CCX 3.0 Task 4.4: the async CCX import handler routes through CcxImporter.

Builds a real CCX 3.0 package with ``CcxExporter`` from a seeded graph, then
drives ``handle_import_ccx`` (the ``OP_IMPORT_CCX`` handler body) with the
base64-encoded bytes and asserts the result dict reports the imported counts
and the conformance classes the package declared.
"""

from __future__ import annotations

import base64
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

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
from chaoscypher_core.operations.importing.format_handler import handle_import_ccx
from chaoscypher_core.services.export import CcxExporter


@pytest.fixture
def sqlite_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Per-test file-backed ``SqliteAdapter`` with all tables created."""
    db_path = tmp_path / "test.db"
    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        yield adapter
    finally:
        adapter.disconnect()


def _seed(adapter: SqliteAdapter, database_name: str = "default") -> None:
    """Seed 1 node template, 1 edge template, 2 nodes, 1 property edge, 1 source + 1 chunk."""
    assert adapter.session is not None
    session = adapter.session
    session.add(
        SourceRow(
            id="src_1",
            database_name=database_name,
            filename="doc.txt",
            filepath="/data/doc.txt",
            title="Doc One",
            source_type="text",
            status="committed",
            full_text="Alice works with Bob.",
        )
    )
    session.flush()
    session.add(
        GraphTemplate(
            id="tpl_person", database_name=database_name, name="Person", template_type="node"
        )
    )
    session.add(
        GraphTemplate(
            id="tpl_rel", database_name=database_name, name="WorksWith", template_type="edge"
        )
    )
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
    session.commit()


def _export_bytes(adapter: SqliteAdapter) -> bytes:
    _seed(adapter)
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


@pytest.mark.asyncio
async def test_handle_import_ccx_reports_counts_and_conformance(
    sqlite_adapter: SqliteAdapter,
) -> None:
    """The handler imports a real CCX 3.0 package and reports counts + classes."""
    data_bytes = _export_bytes(sqlite_adapter)

    assert sqlite_adapter.session is not None
    target_db = "imported"
    graph_repo = GraphRepository(sqlite_adapter.session, target_db)

    result = await handle_import_ccx(
        data={"file_content": base64.b64encode(data_bytes).decode("ascii"), "merge": False},
        graph_repository=graph_repo,
        source_repository=sqlite_adapter,
        metadata={"database_name": target_db},
    )

    assert result["success"] is True
    assert result["nodes_imported"] == 2
    assert result["edges_imported"] == 1
    assert result["sources_imported"] == 1
    assert result["chunks_imported"] == 1
    assert result["templates_imported"] >= 2
    assert "core" in result["conformance_classes"]
    assert "sources" in result["conformance_classes"]
    assert result["checksum_verified"] is True
    assert result["errors"] == []
