# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The MCP server's URL import fetches through the shared ``fetch_page``.

Site import design, decision 1: one page fetch for every URL import.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.services.sources.web import FetchedPage


@pytest.mark.asyncio
async def test_fetch_url_source_fetches_through_fetch_page() -> None:
    from chaoscypher_core.mcp.server import _fetch_url_source

    engine = MagicMock()
    engine.settings.paths.max_filename_length = 255
    page = FetchedPage(
        url="https://example.com/guide",
        final_url="https://example.com/guide",
        content_type="text/html",
        is_binary=False,
        content="# Guide\n\nBody",
        data=None,
        title="Guide",
    )
    fetch = AsyncMock(return_value=page)

    with patch("chaoscypher_core.mcp.server.fetch_page", fetch):
        result = await _fetch_url_source(engine, "https://example.com/guide")

    fetch.assert_awaited_once_with("https://example.com/guide", engine.settings)
    assert result["text"] == "# Guide\n\nBody"
    assert result["title"] == "Guide"
    assert result["file_size"] == len(page.payload)
