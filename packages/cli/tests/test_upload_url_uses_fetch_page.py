# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The CLI's ``upload_url`` fetches through Core's shared ``fetch_page``.

Site import design, decision 1: one page fetch for every URL import. The CLI
hands ``fetch_page`` the URL and its engine settings and stores the page's
content hash.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from chaoscypher_cli.sources.service import CLISourceProcessingService
from chaoscypher_core.services.sources.web import FetchedPage


def test_upload_url_fetches_through_fetch_page(mock_cli_context: MagicMock) -> None:
    page = FetchedPage(
        url="https://example.com/page",
        final_url="https://example.com/page",
        content_type="text/html",
        is_binary=False,
        content="A page body that is comfortably longer than the minimum length.",
        data=None,
        title="A Page",
    )
    fetch = AsyncMock(return_value=page)
    upload_spy = MagicMock()
    mock_cli_context.storage_adapter.upload_source = upload_spy

    with patch("chaoscypher_cli.sources.service.fetch_page", fetch):
        service = CLISourceProcessingService(mock_cli_context)
        _file_id, title = service.upload_url("https://example.com/page")
        service.close()

    fetch.assert_awaited_once_with("https://example.com/page", mock_cli_context.settings)
    assert title == "A Page"
    assert upload_spy.call_args.kwargs["content_hash"] == page.content_hash
