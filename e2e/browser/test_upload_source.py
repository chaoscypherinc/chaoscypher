# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Browser E2E tests for the source upload flow."""

import os

import pytest


try:
    from playwright.sync_api import Page, expect
except ImportError:
    pytest.skip("playwright not installed", allow_module_level=True)


class TestUploadDialog:
    """Test the Add Source dialog interaction."""

    def test_add_source_button_opens_dialog(self, authenticated_page: Page) -> None:
        """Clicking Add Source opens a modal dialog."""
        page = authenticated_page
        page.goto("/sources")
        page.wait_for_load_state("networkidle")

        page.get_by_role("button", name="Add Source").click()

        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible(timeout=5000)
        expect(dialog).to_contain_text("Add Source")

    def test_dialog_has_url_input(self, authenticated_page: Page) -> None:
        """Upload dialog has a URL input field."""
        page = authenticated_page
        page.goto("/sources")
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name="Add Source").click()
        page.wait_for_timeout(500)

        url_input = page.get_by_placeholder("https://example.com/article")
        expect(url_input).to_be_visible()

    def test_dialog_has_file_input(self, authenticated_page: Page) -> None:
        """Upload dialog has a file drop zone with hidden file input."""
        page = authenticated_page
        page.goto("/sources")
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name="Add Source").click()
        page.wait_for_timeout(500)

        file_input = page.locator("input[type='file']")
        assert file_input.count() >= 1

    def test_upload_file_via_dialog(self, authenticated_page: Page, sample_data_dir: str) -> None:
        """Selecting a file via the dialog adds it to the upload list."""
        page = authenticated_page
        page.goto("/sources")
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name="Add Source").click()
        page.wait_for_timeout(500)

        # The dialog renders two ``input[type=file]`` elements (one for
        # drag-and-drop, one behind the explicit "Browse" button), both
        # ``aria-label="Upload files"``. Use ``.first`` to disambiguate
        # — set_input_files works against either backing element.
        file_path = os.path.join(sample_data_dir, "sample.txt")
        page.locator("input[type='file']").first.set_input_files(file_path)
        page.wait_for_timeout(500)

        # The dialog should now show the filename
        dialog = page.get_by_role("dialog")
        dialog_text = dialog.inner_text()
        assert "sample" in dialog_text.lower()

    def test_cancel_closes_dialog(self, authenticated_page: Page) -> None:
        """Clicking Cancel closes the dialog."""
        page = authenticated_page
        page.goto("/sources")
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name="Add Source").click()
        page.wait_for_timeout(500)

        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()

        # Click Cancel button
        cancel_btn = page.get_by_role("button", name="Cancel")
        cancel_btn.click()
        page.wait_for_timeout(500)

        expect(dialog).not_to_be_visible()
