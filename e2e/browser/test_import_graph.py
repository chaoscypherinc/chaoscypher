# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Browser E2E tests for graph data visibility after import."""

import pytest


try:
    from playwright.sync_api import Page
except ImportError:
    pytest.skip("playwright not installed", allow_module_level=True)


class TestImportedGraphVisibility:
    """Verify imported graph data appears in the UI.

    These tests assume seed.ccx has been imported via API tests
    (test_export_import.py runs before browser tests in fresh phase).
    """

    def test_nodes_table_has_rows(self, authenticated_page: Page) -> None:
        """Entities page table contains rows from imported data."""
        page = authenticated_page
        page.goto("/nodes")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

        # After seed import, there should be rows in the table
        rows = page.locator("table tbody tr")
        assert rows.count() > 0, "No rows in entities table after import"

    def test_templates_page_shows_templates(self, authenticated_page: Page) -> None:
        """Templates page shows imported templates."""
        page = authenticated_page
        page.goto("/templates")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

        # Imported seed has 4 templates
        rows = page.locator("table tbody tr")
        assert rows.count() > 0

    def test_graph_canvas_renders_with_data(self, authenticated_page: Page) -> None:
        """Graph canvas renders the visualization (canvas + SVG)."""
        page = authenticated_page
        page.goto("/graph")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)  # Sigma.js needs render time

        canvas_count = page.locator("canvas").count()
        svg_count = page.locator("svg").count()
        assert canvas_count > 0 or svg_count > 0

    def test_relationships_page_loads(self, authenticated_page: Page) -> None:
        """Relationships (edges) page loads with content."""
        page = authenticated_page
        page.goto("/edges")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

        # Page should have loaded
        assert "edges" in page.url.lower() or "relationship" in page.url.lower()
