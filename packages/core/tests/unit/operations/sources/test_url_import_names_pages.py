# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The worker's ``fetch_url`` handler and the MCP URL import name a page through ``page_names``.

One title-to-filename rule for every URL import (root CLAUDE.md rule 3): the
same page title yields the same stored title and filename stem on each surface.
The CLI's side of the parity lives in ``packages/cli/tests``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.operations.sources.url_fetch_handler import handle_fetch_url
from chaoscypher_core.services.sources.web import FetchedPage, page_names


_TITLE = "<b>Getting Started</b>: Install\x07 guide"
_BODY = "A page body long enough to clear the minimum content length check easily."


def _page() -> FetchedPage:
    return FetchedPage(
        url="https://example.com/doc",
        final_url="https://example.com/doc",
        content_type="text/html",
        is_binary=False,
        content=_BODY,
        data=None,
        title=_TITLE,
    )


@pytest.mark.asyncio
async def test_worker_handler_names_the_page_with_page_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = MagicMock()
    settings.batching.max_upload_bytes = 1024 * 1024
    settings.paths.max_filename_length = 80
    monkeypatch.setattr(
        "chaoscypher_core.operations.sources.url_fetch_handler.get_settings", lambda: settings
    )
    sps = MagicMock()
    sps.upload_file = AsyncMock(return_value={"id": "src_new"})

    with patch(
        "chaoscypher_core.operations.sources.url_fetch_handler.fetch_page",
        AsyncMock(return_value=_page()),
    ):
        await handle_fetch_url(
            data={"url": "https://example.com/doc", "options": {}},
            source_processing_service=sps,
            metadata={"database_name": "default"},
        )

    expected = page_names(_TITLE, settings)
    kwargs = sps.upload_file.await_args.kwargs
    assert kwargs["filename"] == f"{expected.stem}.md"
    assert kwargs["title_override"] == expected.title


@pytest.mark.asyncio
async def test_mcp_url_import_names_the_page_with_page_names() -> None:
    from chaoscypher_core.mcp.server import _fetch_url_source

    engine = SimpleNamespace(
        settings=SimpleNamespace(paths=SimpleNamespace(max_filename_length=80))
    )
    with patch("chaoscypher_core.mcp.server.fetch_page", AsyncMock(return_value=_page())):
        result = await _fetch_url_source(engine, "https://example.com/doc")  # type: ignore[arg-type]

    expected = page_names(_TITLE, engine.settings, fallback_title="https://example.com/doc")
    assert result["filename"] == f"{expected.stem}.md"
    assert result["title"] == expected.title
