# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration test: CcxImporter round-trips the chaoscypher.lenses graph.

The exporter routes lens nodes (``template_id == "system_lens"``) out of the
neutral knowledge graph into the ``chaoscypher.lenses`` named graph. This test
seeds a lens node, exports the package, imports it into a SEPARATE database,
and asserts the lens node lands (upsert-by-IRI → idempotent). It fails before
the importer learns to read ``chaoscypher.lenses``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from chaoscypher_core.adapters.sqlite.models import GraphNode, GraphTemplate
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings
from chaoscypher_core.services.export import CcxExporter
from chaoscypher_core.services.package.importer import CcxImporter, ImportOptions


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _seed_lens(adapter: SqliteAdapter, database_name: str = "default") -> None:
    """Seed the system_lens template + one lens node (with properties)."""
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
    session.flush()
    session.add(
        GraphNode(
            id="lens_overview",
            database_name=database_name,
            graph_name="knowledge",
            template_id="system_lens",
            label="Overview Lens",
            properties={"layout": "force", "zoom": 1.5},
        )
    )
    session.commit()


@pytest.mark.asyncio
async def test_ccx_importer_round_trips_lens_node(
    integration_adapter: SqliteAdapter,
) -> None:
    """A lens node survives export -> import into a fresh database."""
    _seed_lens(integration_adapter)
    assert integration_adapter.session is not None

    graph_repo = GraphRepository(integration_adapter.session, "default")
    settings = build_engine_settings(get_settings())
    exporter = CcxExporter(
        graph_repository=graph_repo,
        sources_repository=integration_adapter,
        settings=settings,
        workflow_db=None,
    )
    data = exporter.export(include_embeddings=False, include_lenses=True)

    target_db = "imported_lenses"
    target_repo = GraphRepository(integration_adapter.session, target_db)
    importer = CcxImporter(
        graph_repository=target_repo,
        sources_repository=integration_adapter,
        workflow_db=None,
    )
    stats = await importer.import_from_bytes(data, ImportOptions(database_name=target_db))
    assert not stats.errors, stats.errors

    # The lens node landed under the target database, keyed by its CCX IRI.
    imported = target_repo.get_node_by_ccx_iri("urn:ccx:chaoscypher:node/lens_overview", target_db)
    assert imported is not None, "lens node was dropped on import"
    assert imported["label"] == "Overview Lens"
    assert imported["properties"].get("layout") == "force"

    counts_before = len(target_repo.list_nodes(include_disabled_sources=True))

    # Re-import the SAME bytes: idempotent upsert-by-IRI, no duplicate lens.
    await importer.import_from_bytes(data, ImportOptions(database_name=target_db))
    counts_after = len(target_repo.list_nodes(include_disabled_sources=True))
    assert counts_after == counts_before


@pytest.mark.asyncio
async def test_ccx_importer_parses_graph_documents_once_per_import(
    integration_adapter: SqliteAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One import parses the package's graphs ONCE, not once per pass.

    ``graph_documents()`` decompresses and json-parses every graph (no cache in
    ccx-format); the templates / knowledge / lenses / workflows passes each used
    to call it just to find their one graph.
    """
    import ccx.package

    _seed_lens(integration_adapter)
    assert integration_adapter.session is not None
    graph_repo = GraphRepository(integration_adapter.session, "default")
    exporter = CcxExporter(
        graph_repository=graph_repo,
        sources_repository=integration_adapter,
        settings=build_engine_settings(get_settings()),
        workflow_db=None,
    )
    data = exporter.export(include_embeddings=False, include_lenses=True)

    calls = 0
    original = ccx.package.CCXPackage.graph_documents

    def _counting(self: ccx.package.CCXPackage) -> list[ccx.package.GraphDocument]:
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(ccx.package.CCXPackage, "graph_documents", _counting)

    target_db = "imported_parse_once"
    target_repo = GraphRepository(integration_adapter.session, target_db)
    importer = CcxImporter(
        graph_repository=target_repo,
        sources_repository=integration_adapter,
        workflow_db=None,
    )
    stats = await importer.import_from_bytes(data, ImportOptions(database_name=target_db))

    assert not stats.errors, stats.errors
    assert target_repo.get_node_by_ccx_iri("urn:ccx:chaoscypher:node/lens_overview", target_db)
    assert calls == 1
