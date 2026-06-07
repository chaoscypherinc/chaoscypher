# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: URL fetch handler tempfile write does not regress happy path."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_url_fetch_happy_path_writes_to_disk_via_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end happy path with all I/O mocked + a non-stub asyncio.to_thread."""
    from chaoscypher_core.operations.sources.url_fetch_handler import handle_fetch_url

    settings = MagicMock()
    settings.batching.max_upload_bytes = 1024 * 1024
    settings.batching.upload_content_type_allowlist = [
        "text/html",
        "text/plain",
        "text/markdown",
        "application/pdf",
    ]
    monkeypatch.setattr(
        "chaoscypher_core.operations.sources.url_fetch_handler.get_settings",
        lambda: settings,
    )

    storage = MagicMock()
    storage.create_url_placeholder = MagicMock()
    storage.delete_source = MagicMock()

    sps = MagicMock()
    sps.source_manager = storage
    sps.config_manager = MagicMock()
    sps.upload_file = AsyncMock(
        return_value={"id": "src_new", "filename": "doc.md", "status": "pending"},
    )

    # Spy on asyncio.to_thread so we can assert the tempfile write goes through it.
    real_to_thread = asyncio.to_thread
    to_thread_calls: list[str] = []

    async def spy(func, *args, **kwargs):
        to_thread_calls.append(getattr(func, "__name__", str(func)))
        return await real_to_thread(func, *args, **kwargs)

    with (
        patch("chaoscypher_core.adapters.web.search.WebScraper") as mock_scraper,
        patch(
            "chaoscypher_core.operations.sources.url_fetch_handler.asyncio.to_thread",
            side_effect=spy,
        ),
    ):
        from chaoscypher_core.adapters.web.search import FetchResult

        scraper_instance = mock_scraper.return_value
        scraper_instance.extract_full_content = AsyncMock(
            return_value=FetchResult(
                content="Real content body that is long enough to pass the min check." * 50,
                content_type="text/html",
                title="Doc",
                error=None,
            ),
        )

        result = await handle_fetch_url(
            data={"url": "https://example.com/x", "options": {}},
            source_processing_service=sps,
            metadata={"database_name": "default", "operation_type": "fetch_url"},
            task_id="t1",
        )

    assert result["id"] == "src_new"
    # Confirm the tempfile write went through to_thread (any name match is fine —
    # the wrapper closure or the underlying writer).
    assert to_thread_calls, "Expected at least one asyncio.to_thread call for tempfile write"
