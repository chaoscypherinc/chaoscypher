# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests: a seeded graph exports to a conformant CCX 3.0 bundle.

Full flow: seed graph → CcxExporter.export() → ccx.open_package(bytes) → assert:
  - the package self-validates (Core conformance) via ccx-format
  - the manifest reports ccx_version 3.0
  - the on-disk bundle shape is CCX 3.0: ``manifest.json`` + ``context.jsonld``
    + ``knowledge.jsonld`` present, NO ``README.txt`` (CCX has no README; the
    v2.0 manifest-dict + README bundle shape is gone)
  - the knowledge default graph + the chaoscypher.statistics graph are present
  - a title flows through to the manifest
  - the optional ``graph_preview.png`` is emitted as a content-addressed asset
    only when preview bytes are supplied (the v2.0 preview functionality, now a
    CCX 3.0 asset).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ccx

from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings
from chaoscypher_core.services.export import CcxExporter

from ....fixtures.seed_graph import seed_two_sources_three_templates


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


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
    )


def test_export_produces_conformant_ccx_package(
    integration_adapter: SqliteAdapter,
) -> None:
    """Seed graph → export → bytes validate as CCX 3.0 with the core class."""
    seed = seed_two_sources_three_templates(integration_adapter)

    data = _exporter(integration_adapter).export()

    pkg = ccx.open_package(data)
    report = pkg.validate()
    assert report.ok, report.errors
    assert "core" in report.classes
    assert pkg.manifest.ccx_version == "3.0"

    # The knowledge default graph is present and carries the seeded nodes.
    know = next(g for g in pkg.graph_documents() if g.role == "default")
    knowledge_node_iris = {
        obj["@id"] for obj in know.doc["@graph"] if obj.get("@type") != "ccx:Relationship"
    }
    assert len(knowledge_node_iris) == seed.total_nodes

    # The chaoscypher.statistics named graph is present.
    statistics = next(
        g for g in pkg.graph_documents() if (g.namespace, g.name) == ("chaoscypher", "statistics")
    )
    assert statistics.doc["@graph"]


def test_export_bundle_shape_is_ccx_3_0(
    integration_adapter: SqliteAdapter,
) -> None:
    """The on-disk bundle is CCX 3.0: manifest/context/knowledge present, no README."""
    seed_two_sources_three_templates(integration_adapter)

    data = _exporter(integration_adapter).export()

    pkg = ccx.open_package(data)
    names = set(pkg.container.names())
    assert "manifest.json" in names
    assert "context.jsonld" in names
    assert "knowledge.jsonld" in names
    # CCX has no README.txt (the v2.0 README was dropped; ``ccx inspect`` covers it).
    assert "README.txt" not in names


def test_export_title_flows_to_manifest(
    integration_adapter: SqliteAdapter,
) -> None:
    """title= parameter reaches the CCX manifest title."""
    seed_two_sources_three_templates(integration_adapter)

    data = _exporter(integration_adapter).export(title="My Export Title")

    pkg = ccx.open_package(data)
    assert pkg.validate().ok
    assert pkg.manifest.title == "My Export Title"


def test_export_emits_graph_preview_asset_when_supplied(
    integration_adapter: SqliteAdapter,
) -> None:
    """preview_png bytes → graph_preview.png asset present; package still validates."""
    seed_two_sources_three_templates(integration_adapter)

    data = _exporter(integration_adapter).export(preview_png=_PNG_BYTES)

    pkg = ccx.open_package(data)
    assert pkg.validate().ok, pkg.validate().errors
    asset_paths = {a.path for a in pkg.manifest.assets}
    assert _PREVIEW_ASSET_PATH in asset_paths
    assert pkg.asset_bytes(_PREVIEW_ASSET_PATH) == _PNG_BYTES


def test_export_omits_graph_preview_asset_by_default(
    integration_adapter: SqliteAdapter,
) -> None:
    """No preview_png → no graph_preview asset."""
    seed_two_sources_three_templates(integration_adapter)

    data = _exporter(integration_adapter).export()

    pkg = ccx.open_package(data)
    asset_paths = {a.path for a in pkg.manifest.assets}
    assert _PREVIEW_ASSET_PATH not in asset_paths
