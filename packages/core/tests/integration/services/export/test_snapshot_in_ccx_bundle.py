# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests: graph snapshot + PNG preview packed into .ccx bundle.

Full flow: seed graph → run export_graph → open .ccx → assert:
  - manifest.json present and carries the GraphBreakdown shape
  - graph_preview.png present and is a valid PNG
  - manifest.contents[] references graph_preview.png with correct checksums
"""

from __future__ import annotations

import json
import zipfile
from typing import TYPE_CHECKING

from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.services.export.management.service import ExportRepository

from ....fixtures.seed_graph import seed_two_sources_three_templates


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


# PNG magic bytes: first 8 bytes of any valid PNG file.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# Test 1: bundle contains manifest.json + graph_preview.png
# ---------------------------------------------------------------------------


def test_export_bundle_contains_manifest_and_preview(
    integration_adapter: SqliteAdapter,
) -> None:
    """Seed graph → export_graph → .ccx must contain manifest.json + graph_preview.png."""
    seed = seed_two_sources_three_templates(integration_adapter)

    assert integration_adapter.session is not None
    graph_repo = GraphRepository(integration_adapter.session, "default")

    from chaoscypher_core.app_config import get_settings

    settings = get_settings()

    repo = ExportRepository(
        graph_repository=graph_repo,
        settings=settings,
        adapter=integration_adapter,
    )

    zip_buffer = repo.export_graph()
    zip_buffer.seek(0)

    with zipfile.ZipFile(zip_buffer, "r") as zf:
        names = set(zf.namelist())

        assert "manifest.json" in names, "manifest.json must be present in bundle"
        assert "graph_preview.png" in names, "graph_preview.png must be present in bundle"

        # --- Validate manifest carries GraphBreakdown shape ---
        manifest_data = json.loads(zf.read("manifest.json"))

        assert manifest_data.get("ccx_version") == "2.0"
        assert "stats" in manifest_data, "manifest must carry stats from GraphBreakdown"
        assert "sources" in manifest_data, "manifest must carry sources from GraphBreakdown"

        assert manifest_data["stats"]["total_nodes"] == seed.total_nodes
        assert manifest_data["stats"]["total_sources"] == 2
        assert len(manifest_data["sources"]) == 2

        # --- Preview PNG is a valid PNG file ---
        preview = zf.read("graph_preview.png")
        assert preview[:8] == _PNG_MAGIC, "graph_preview.png must start with PNG magic bytes"
        assert len(preview) > 1024, "graph_preview.png must be a non-trivial PNG (>1 KB)"

        # --- manifest.contents[] references graph_preview ---
        contents = manifest_data.get("contents", [])
        preview_entry = next((c for c in contents if c.get("type") == "graph_preview"), None)
        assert preview_entry is not None, "manifest.contents[] must include a graph_preview entry"
        assert preview_entry["path"] == "graph_preview.png"
        assert preview_entry["media_type"] == "image/png"
        assert preview_entry["file_size_bytes"] == len(preview)

        # --- graph_preview appears in package_type list ---
        assert "graph_preview" in manifest_data.get("package_type", [])


# ---------------------------------------------------------------------------
# Test 2: bundle with no adapter falls back gracefully (no PNG)
# ---------------------------------------------------------------------------


def test_export_bundle_without_adapter_omits_preview(
    integration_adapter: SqliteAdapter,
) -> None:
    """When no adapter is provided, graph_preview.png is absent from the bundle."""
    seed_two_sources_three_templates(integration_adapter)

    assert integration_adapter.session is not None
    graph_repo = GraphRepository(integration_adapter.session, "default")

    from chaoscypher_core.app_config import get_settings

    settings = get_settings()

    # No adapter — snapshot/preview skipped
    repo = ExportRepository(
        graph_repository=graph_repo,
        settings=settings,
        adapter=None,
    )

    zip_buffer = repo.export_graph()
    zip_buffer.seek(0)

    with zipfile.ZipFile(zip_buffer, "r") as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "graph_preview.png" not in names, (
            "graph_preview.png must be absent when adapter is None"
        )

        manifest_data = json.loads(zf.read("manifest.json"))
        contents = manifest_data.get("contents", [])
        preview_entry = next((c for c in contents if c.get("type") == "graph_preview"), None)
        assert preview_entry is None, (
            "manifest.contents[] must not include graph_preview when adapter is None"
        )


# ---------------------------------------------------------------------------
# Test 3: title flows through to manifest
# ---------------------------------------------------------------------------


def test_export_bundle_title_flows_to_manifest(
    integration_adapter: SqliteAdapter,
) -> None:
    """title= parameter reaches manifest.title via GraphBreakdown."""
    seed_two_sources_three_templates(integration_adapter)

    assert integration_adapter.session is not None
    graph_repo = GraphRepository(integration_adapter.session, "default")

    from chaoscypher_core.app_config import get_settings

    settings = get_settings()

    repo = ExportRepository(
        graph_repository=graph_repo,
        settings=settings,
        adapter=integration_adapter,
    )

    zip_buffer = repo.export_graph(title="My Export Title")
    zip_buffer.seek(0)

    with zipfile.ZipFile(zip_buffer, "r") as zf:
        manifest_data = json.loads(zf.read("manifest.json"))
        assert manifest_data.get("title") == "My Export Title"
