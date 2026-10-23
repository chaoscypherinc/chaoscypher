# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The worker's ``fetch_url`` handler fetches through the shared ``fetch_page``.

Site import design, decision 1: one page fetch for every URL import. The
handler hands ``fetch_page`` the URL and its settings, then stages exactly the
page's payload under the page's content hash.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.operations.sources.url_fetch_handler import handle_fetch_url
from chaoscypher_core.services.sources.web import FetchedPage


@pytest.mark.asyncio
async def test_handler_fetches_through_fetch_page(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = MagicMock()
    settings.batching.max_upload_bytes = 1024 * 1024
    monkeypatch.setattr(
        "chaoscypher_core.operations.sources.url_fetch_handler.get_settings", lambda: settings
    )
    sps = MagicMock()
    sps.upload_file = AsyncMock(return_value={"id": "src_new"})
    page = FetchedPage(
        url="https://example.com/doc",
        final_url="https://example.com/doc/",
        content_type="text/html",
        is_binary=False,
        content="Shared fetch body long enough to clear the minimum length check.",
        data=None,
        title="Doc",
    )
    fetch = AsyncMock(return_value=page)

    with patch("chaoscypher_core.operations.sources.url_fetch_handler.fetch_page", fetch):
        await handle_fetch_url(
            data={"url": "https://example.com/doc", "options": {}},
            source_processing_service=sps,
            metadata={"database_name": "default"},
        )

    fetch.assert_awaited_once_with("https://example.com/doc", settings)
    upload_kwargs = sps.upload_file.await_args.kwargs
    assert upload_kwargs["content_hash"] == page.content_hash
    assert upload_kwargs["file_size"] == len(page.payload)
