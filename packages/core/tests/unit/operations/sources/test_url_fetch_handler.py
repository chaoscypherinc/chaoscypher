# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: URL placeholder must remain visible if upload_file fails."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.operations.sources.url_fetch_handler import handle_fetch_url


def _make_sps(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Return (sps, storage) mocks with settings wired up."""
    storage = MagicMock()
    config_manager = MagicMock()
    config_manager.get_settings.return_value = MagicMock(current_database="default")

    settings = MagicMock()
    settings.batching.max_upload_bytes = 1024 * 1024
    monkeypatch.setattr(
        "chaoscypher_core.operations.sources.url_fetch_handler.get_settings",
        lambda: settings,
    )

    sps = MagicMock()
    sps.source_manager = storage
    sps.config_manager = config_manager
    return sps, storage


@pytest.mark.asyncio
async def test_placeholder_marked_error_when_upload_file_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """upload_file failure: placeholder NOT deleted; fail_url_fetch called."""
    sps, storage = _make_sps(monkeypatch)
    sps.upload_file = AsyncMock(side_effect=ValidationError("bad", field="file"))

    long_content = "Real content that is long enough to pass the minimum check. " * 20

    from chaoscypher_core.adapters.web.search import FetchResult

    with patch("chaoscypher_core.adapters.web.search.WebScraper") as mock_scraper:
        scraper_instance = mock_scraper.return_value
        scraper_instance.extract_full_content = AsyncMock(
            return_value=FetchResult(
                content=long_content,
                content_type="text/html",
                title="T",
                error=None,
            )
        )

        with pytest.raises(ValidationError):
            await handle_fetch_url(
                data={"url": "https://example.com/", "options": {}},
                source_processing_service=sps,
                metadata={"database_name": "default", "operation_type": "fetch_url"},
                task_id="tsk_1",
            )

    # delete_source must NOT have been called pre-upload — the placeholder
    # is the visibility row, and it must survive into the failure path so
    # fail_url_fetch can promote it to ERROR.
    storage.delete_source.assert_not_called()
    storage.fail_url_fetch.assert_called_once()


@pytest.mark.asyncio
async def test_placeholder_deleted_on_successful_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful upload: placeholder deleted; fail_url_fetch not called."""
    sps, storage = _make_sps(monkeypatch)
    sps.upload_file = AsyncMock(return_value={"id": "src_new", "filename": "T.md"})

    long_content = "Real content that is long enough to pass the minimum check. " * 20

    from chaoscypher_core.adapters.web.search import FetchResult

    with patch("chaoscypher_core.adapters.web.search.WebScraper") as mock_scraper:
        scraper_instance = mock_scraper.return_value
        scraper_instance.extract_full_content = AsyncMock(
            return_value=FetchResult(
                content=long_content,
                content_type="text/html",
                title="T",
                error=None,
            )
        )

        result = await handle_fetch_url(
            data={"url": "https://example.com/", "options": {}},
            source_processing_service=sps,
            metadata={"database_name": "default", "operation_type": "fetch_url"},
            task_id="tsk_1",
        )

    assert result == {"id": "src_new", "filename": "T.md"}
    storage.delete_source.assert_called_once()
    storage.fail_url_fetch.assert_not_called()
