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
        """Graph canvas renders the visualization (Sigma.js canvas)."""
        page = authenticated_page
        page.goto("/graph")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)  # Sigma.js needs render time

        # A page-wide `svg` count is satisfied by the app shell's own
        # icons (Sidebar.tsx's MUI ChevronLeftIcon/ChevronRightIcon at
        # :27-28/:306-308 and lucide nav icons at :81-98), which render
        # on every route via Layout.tsx's <Box component="nav"> -- that
        # made the canvas half of the old `or` unreachable as a failure
        # mode. Layout.tsx's <Box component="main"> (:244) renders as a
        # real <main> element and exclusively wraps the routed page's own
        # content (the Sidebar's <nav> is a separate sibling, not nested
        # inside it), so scoping to `main canvas` excludes the sidebar
        # without needing a new data-testid. GraphCanvasPage.tsx mounts
        # Sigma (WebGL-only, canvas-based -- see its own webglSupported
        # comment) inside that <main>, so this asserts the canvas itself,
        # not any page-wide svg.
        canvas_count = page.locator("main canvas").count()
        assert canvas_count > 0, "No canvas rendered inside the page's <main> content area"

    def test_relationships_page_loads(self, authenticated_page: Page) -> None:
        """Relationships (edges) page loads with content."""
        page = authenticated_page
        page.goto("/edges")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

        # EdgesPage (packages/interface/src/pages/EdgesPage/index.tsx:248)
        # renders an <h4>Relationships</h4> heading unconditionally once
        # loading finishes -- assert on that page-specific content
        # instead of the URL the test itself just navigated to.
        heading = page.locator("h4", has_text="Relationships")
        assert heading.count() > 0, "Relationships heading not found on /edges"
