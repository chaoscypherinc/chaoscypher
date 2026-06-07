# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: URL fetch handler reads database_name from queue task metadata, not worker config."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_url_fetch_uses_metadata_database_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker's current_database is 'B' but metadata says 'A' — placeholder lands in 'A'."""
    from chaoscypher_core.operations.sources.url_fetch_handler import handle_fetch_url

    settings = MagicMock()
    settings.batching.max_upload_bytes = 1024 * 1024
    monkeypatch.setattr(
        "chaoscypher_core.operations.sources.url_fetch_handler.get_settings",
        lambda: settings,
    )

    storage = MagicMock()
    storage.create_url_placeholder = MagicMock()
    storage.fail_url_fetch = MagicMock()
    storage.delete_source = MagicMock()

    config_settings = MagicMock()
    config_settings.current_database = "B"  # worker's local view

    config_manager = MagicMock()
    config_manager.get_settings.return_value = config_settings

    sps = MagicMock()
    sps.source_manager = storage
    sps.config_manager = config_manager
    sps.upload_file = AsyncMock(
        return_value={"id": "src_new", "filename": "doc.md", "status": "pending"},
    )

    from chaoscypher_core.adapters.web.search import FetchResult

    scraper = MagicMock()
    scraper.extract_full_content = AsyncMock(
        return_value=FetchResult(
            content="x" * 1000,
            content_type="text/html",
            title="Doc",
            error=None,
        ),
    )

    with patch(
        "chaoscypher_core.adapters.web.search.WebScraper",
        return_value=scraper,
    ):
        await handle_fetch_url(
            data={"url": "https://example.com/x", "options": {}},
            source_processing_service=sps,
            metadata={"database_name": "A", "operation_type": "fetch_url"},
            task_id="task_1",
        )

    # Placeholder must be created in DB "A" (from metadata), not "B" (from worker config).
    storage.create_url_placeholder.assert_called_once()
    kwargs = storage.create_url_placeholder.call_args.kwargs
    assert kwargs["database_name"] == "A"


@pytest.mark.asyncio
async def test_url_fetch_raises_when_metadata_lacks_database_name() -> None:
    """Queue contract requires database_name — handler fails loudly if absent."""
    from chaoscypher_core.operations.sources.url_fetch_handler import handle_fetch_url

    sps = MagicMock()
    sps.source_manager = MagicMock()
    sps.config_manager = MagicMock()

    with pytest.raises((KeyError, ValueError)):
        await handle_fetch_url(
            data={"url": "https://example.com/x", "options": {}},
            source_processing_service=sps,
            metadata={},  # missing database_name — contract violation
            task_id="task_1",
        )
