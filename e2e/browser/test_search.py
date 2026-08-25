# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Browser E2E tests for search/omnibar UI."""

import pytest


try:
    from playwright.sync_api import Page, expect
except ImportError:
    pytest.skip("playwright not installed", allow_module_level=True)


class TestOmnibar:
    """Test the Ctrl+K omnibar/command palette."""

    def test_ctrl_k_opens_omnibar(self, authenticated_page: Page) -> None:
        """Pressing Ctrl+K opens an omnibar input."""
        page = authenticated_page
        page.goto("/")
        page.wait_for_load_state("networkidle")

        page.keyboard.press("Control+k")
        page.wait_for_timeout(500)

        # An input should be visible after the keystroke
        visible_inputs = page.locator("input[type='text']:visible").count()
        assert visible_inputs >= 1, "Omnibar input not visible after Ctrl+K"

    def test_omnibar_accepts_query(self, authenticated_page: Page) -> None:
        """Omnibar input accepts typed text."""
        page = authenticated_page
        page.goto("/")
        page.wait_for_load_state("networkidle")

        page.keyboard.press("Control+k")
        page.wait_for_timeout(500)

        # Locate the omnibar's own input specifically (same selector the
        # sibling test_ctrl_k_opens_omnibar uses to confirm it opened) --
        # MUI's InputBase defaults its `type` prop to 'text'
        # (Omnibar.tsx's <InputBase inputRef={inputRef} .../>), so this
        # resolves to that one element, not <body>.
        omnibar_input = page.locator("input[type='text']:visible")
        expect(omnibar_input).to_be_visible()

        # Type a search query
        page.keyboard.type("alice")
        page.wait_for_timeout(500)

        # The input should actually contain the typed text.
        assert omnibar_input.input_value() == "alice"

    def test_escape_closes_omnibar(self, authenticated_page: Page) -> None:
        """Escape key closes the omnibar."""
        page = authenticated_page
        page.goto("/")
        page.wait_for_load_state("networkidle")

        page.keyboard.press("Control+k")
        page.wait_for_timeout(500)
        before = page.locator("input[type='text']:visible").count()
        assert before >= 1, "Omnibar did not open after Ctrl+K"

        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        after = page.locator("input[type='text']:visible").count()

        # Omnibar.tsx unmounts entirely when closed (`if (!isOpen ||
        # !anchorEl) return null`), so the input should be fully gone,
        # not merely "fewer than before" -- `after <= before` would also
        # pass if Escape did nothing (before == after) or if Ctrl+K never
        # opened anything at all (before == after == 0).
        assert after == 0, (
            f"Omnibar input still visible after Escape (before={before}, after={after})"
        )
