# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Browser E2E tests for the backup/export flow.

Tests the actual flow that hit the original export bug:
Settings page -> Backup tab -> Create Backup Now button.
"""

import pytest


try:
    from playwright.sync_api import Page, expect
except ImportError:
    pytest.skip("playwright not installed", allow_module_level=True)


def _open_settings_with_retry(page: Page, max_attempts: int = 3) -> None:
    """Navigate to Settings and wait for tabs to render, retrying if needed.

    Settings page is React-lazy-loaded and occasionally fails to render
    tabs on the first visit after many prior browser contexts have been
    created in the same session. Retry with a fresh navigation helps.
    """
    for attempt in range(max_attempts):
        page.goto("/settings")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)  # React lazy-renders the tabs

        # Check if tabs rendered
        if page.locator("[role='tab']").count() > 0:
            return

        # Reload and try again
        if attempt < max_attempts - 1:
            page.reload()
            page.wait_for_timeout(1000)

    msg = "Settings tabs never rendered after retries"
    raise AssertionError(msg)


class TestBackupTab:
    """Test the Backup tab on the Settings page."""

    def test_click_backup_tab_shows_create_button(self, authenticated_page: Page) -> None:
        """Clicking Backup tab reveals the 'Create Backup Now' button."""
        page = authenticated_page
        _open_settings_with_retry(page)

        page.get_by_role("tab", name="Backup").click()
        page.wait_for_timeout(500)

        create_btn = page.get_by_role("button", name="Create Backup Now")
        expect(create_btn).to_be_visible(timeout=5000)

    def test_create_backup_button_clickable(self, authenticated_page: Page) -> None:
        """The Create Backup Now button is enabled and clickable.

        We verify the click triggers the backup API call without
        waiting for the actual backup file to be created.
        """
        page = authenticated_page

        # Track network requests to verify the API call
        backup_requests: list[str] = []
        page.on(
            "request",
            lambda r: backup_requests.append(r.url) if "/api/v1/backup" in r.url else None,
        )

        _open_settings_with_retry(page)
        page.get_by_role("tab", name="Backup").click()
        page.wait_for_timeout(500)

        create_btn = page.get_by_role("button", name="Create Backup Now")
        create_btn.click()
        page.wait_for_timeout(2000)  # Allow API call to fire

        # Verify backup endpoint was hit
        backup_post = [r for r in backup_requests if "backup" in r and "download" not in r]
        assert len(backup_post) > 0, f"Backup API not called. Requests: {backup_requests}"
