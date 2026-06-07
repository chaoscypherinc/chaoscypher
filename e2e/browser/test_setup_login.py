# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Browser E2E tests for login and main navigation."""

import pytest


try:
    from playwright.sync_api import Page, expect
except ImportError:
    pytest.skip("playwright not installed", allow_module_level=True)


class TestLoginFlow:
    """Test the login UI flow."""

    def test_login_page_has_form(self, page: Page) -> None:
        """Login page renders username and password fields."""
        page.goto("/login")
        page.wait_for_load_state("networkidle")

        # ``get_by_label("Password")`` is ambiguous: the LoginPage has
        # a visible ``Show password`` IconButton next to the input,
        # and both surface "Password" in their accessible name. Use
        # role-scoped locators to disambiguate.
        username = page.get_by_role("textbox", name="Username")
        password = page.get_by_role("textbox", name="Password")
        login_btn = page.get_by_role("button", name="Log In")

        expect(username).to_be_visible()
        expect(password).to_be_visible()
        expect(login_btn).to_be_visible()

    def test_login_with_valid_credentials(self, page: Page, browser_session_cookie: str) -> None:
        """Filling form and clicking Log In navigates away from /login."""
        # ``browser_session_cookie`` is requested so the admin user is
        # guaranteed to exist before the form submission below.
        _ = browser_session_cookie

        page.goto("/login")
        page.wait_for_load_state("networkidle")

        page.get_by_role("textbox", name="Username").fill("e2e_admin")
        page.get_by_role("textbox", name="Password").fill("E2eTestPass123")
        page.get_by_role("button", name="Log In").click()

        # Should navigate to dashboard
        page.wait_for_url("**/", timeout=10000)
        assert "/login" not in page.url

    def test_login_navigates_to_dashboard(
        self, page: Page, browser_session_cookie: str
    ) -> None:
        """After login, the page navigates to the dashboard (not /login).

        Auth migrated from bearer tokens in localStorage to a
        server-set httpOnly ``cc_session`` cookie. We can't directly
        assert the cookie value here — Chromium drops the Secure
        attribute on plain-HTTP test stacks. Instead we verify the
        functional outcome: after submitting the form, the React
        app's /auth/me probe succeeded and we left the /login route.
        """
        _ = browser_session_cookie

        page.goto("/login")
        page.wait_for_load_state("networkidle")

        page.get_by_role("textbox", name="Username").fill("e2e_admin")
        page.get_by_role("textbox", name="Password").fill("E2eTestPass123")
        page.get_by_role("button", name="Log In").click()
        page.wait_for_url("**/", timeout=10000)

        assert "/login" not in page.url


class TestNavigation:
    """Test navigation to main app pages after auth."""

    def test_sources_page_loads(self, authenticated_page: Page) -> None:
        """Sources page renders the Add Source button."""
        page = authenticated_page
        page.goto("/sources")
        page.wait_for_load_state("networkidle")

        add_button = page.get_by_role("button", name="Add Source")
        expect(add_button).to_be_visible(timeout=10000)

    def test_nodes_page_renders_table(self, authenticated_page: Page) -> None:
        """Nodes page renders a table."""
        page = authenticated_page
        page.goto("/nodes")
        page.wait_for_load_state("networkidle")

        table = page.locator("table").first
        expect(table).to_be_visible(timeout=10000)

    def test_templates_page_renders_table(self, authenticated_page: Page) -> None:
        """Templates page renders a table."""
        page = authenticated_page
        page.goto("/templates")
        page.wait_for_load_state("networkidle")

        table = page.locator("table").first
        expect(table).to_be_visible(timeout=10000)

    def test_settings_page_has_tabs(self, authenticated_page: Page) -> None:
        """Settings page has multiple navigation tabs."""
        page = authenticated_page
        page.goto("/settings")
        page.wait_for_load_state("networkidle")

        tabs = page.get_by_role("tab")
        # Settings has General, Models, Search, Access, Maintenance, Backup, Logs
        assert tabs.count() >= 5

    def test_graph_page_renders_visualization(self, authenticated_page: Page) -> None:
        """Graph page renders canvas/SVG visualization elements."""
        page = authenticated_page
        page.goto("/graph")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)  # Allow Sigma.js to render

        canvas_count = page.locator("canvas").count()
        svg_count = page.locator("svg").count()
        assert canvas_count > 0 or svg_count > 0
