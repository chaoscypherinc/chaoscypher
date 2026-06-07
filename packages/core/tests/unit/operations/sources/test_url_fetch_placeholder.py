# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: URL fetch creates placeholder, then succeeds or marks ERROR."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.adapters.web.search import FetchResult
from chaoscypher_core.operations.sources.url_fetch_handler import handle_fetch_url


def _make_sps(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Return (sps, storage) mocks with settings wired up.

    Also patches ``get_settings`` in the handler module so it returns a
    mock with ``batching.max_upload_bytes = 1 MiB``.
    """
    storage = MagicMock()
    config_manager = MagicMock()
    config_manager.get_settings.return_value = MagicMock(current_database="default")

    settings = MagicMock()
    settings.batching.max_upload_bytes = 1024 * 1024  # 1 MiB
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

    sps = MagicMock()
    sps.source_manager = storage
    sps.config_manager = config_manager
    return sps, storage


@pytest.mark.asyncio
async def test_fetch_failure_marks_placeholder_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed scrape: placeholder created, then marked ERROR with error_stage='url_fetch'."""
    sps, storage = _make_sps(monkeypatch)

    with patch("chaoscypher_core.adapters.web.search.WebScraper") as mock_scraper:
        scraper_instance = mock_scraper.return_value
        scraper_instance.extract_full_content = AsyncMock(
            return_value=FetchResult(
                content="",
                content_type="",
                url="https://example.com",
                error="404 Not Found",
            )
        )

        result = await handle_fetch_url(
            data={"url": "https://example.com", "options": {}},
            source_processing_service=sps,
            metadata={"database_name": "default", "operation_type": "fetch_url"},
        )

    assert result["status"] == "error"
    storage.create_url_placeholder.assert_called_once()
    storage.fail_url_fetch.assert_called_once()
    # The error message should mention the underlying scraper error
    _placeholder_id, error_msg, db_name = storage.fail_url_fetch.call_args[0]
    assert db_name == "default"
    assert "404" in error_msg


@pytest.mark.asyncio
async def test_fetch_short_content_marks_placeholder_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content below _MIN_CONTENT_BYTES: placeholder marked errored with helpful message."""
    sps, storage = _make_sps(monkeypatch)

    with patch("chaoscypher_core.adapters.web.search.WebScraper") as mock_scraper:
        scraper_instance = mock_scraper.return_value
        scraper_instance.extract_full_content = AsyncMock(
            return_value=FetchResult(
                content="tiny",
                content_type="text/html",
                title="tiny page",
                error=None,
            )
        )

        result = await handle_fetch_url(
            data={"url": "https://example.com", "options": {}},
            source_processing_service=sps,
            metadata={"database_name": "default", "operation_type": "fetch_url"},
        )

    assert result["status"] == "error"
    storage.create_url_placeholder.assert_called_once()
    storage.fail_url_fetch.assert_called_once()
    _placeholder_id, error_msg, db_name = storage.fail_url_fetch.call_args[0]
    assert db_name == "default"
    assert "too short" in error_msg.lower() or "empty" in error_msg.lower()


@pytest.mark.asyncio
async def test_fetch_success_deletes_placeholder_and_uploads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful fetch: placeholder deleted, upload_file called with staged path."""
    sps, storage = _make_sps(monkeypatch)
    storage.delete_source = MagicMock()
    sps.upload_file = AsyncMock(return_value={"id": "src_real", "status": "pending"})

    long_content = "Real content that is long enough to pass the minimum check. " * 20

    with patch("chaoscypher_core.adapters.web.search.WebScraper") as mock_scraper:
        scraper_instance = mock_scraper.return_value
        scraper_instance.extract_full_content = AsyncMock(
            return_value=FetchResult(
                content=long_content,
                content_type="text/html",
                title="Example Page",
                error=None,
            )
        )

        result = await handle_fetch_url(
            data={"url": "https://example.com", "options": {"auto_analyze": True}},
            source_processing_service=sps,
            metadata={"database_name": "default", "operation_type": "fetch_url"},
        )

    assert result["id"] == "src_real"
    storage.create_url_placeholder.assert_called_once()
    storage.delete_source.assert_called_once()
    sps.upload_file.assert_awaited_once()
    # Confirm staged_file_path was passed, not file_content
    call_kwargs = sps.upload_file.await_args.kwargs
    assert "staged_file_path" in call_kwargs
    assert call_kwargs.get("origin_url") == "https://example.com"
    assert call_kwargs.get("source_type_override") == "webpage"
    assert call_kwargs.get("title_override") == "Example Page"


@pytest.mark.asyncio
async def test_unexpected_exception_marks_placeholder_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected exception during fetch is caught, placeholder marked ERROR, then re-raised."""
    sps, storage = _make_sps(monkeypatch)

    with patch("chaoscypher_core.adapters.web.search.WebScraper") as mock_scraper:
        scraper_instance = mock_scraper.return_value
        scraper_instance.extract_full_content = AsyncMock(
            side_effect=RuntimeError("network timeout")
        )

        with pytest.raises(RuntimeError, match="network timeout"):
            await handle_fetch_url(
                data={"url": "https://example.com", "options": {}},
                source_processing_service=sps,
                metadata={"database_name": "default", "operation_type": "fetch_url"},
            )

    storage.create_url_placeholder.assert_called_once()
    storage.fail_url_fetch.assert_called_once()
    _placeholder_id, error_msg, db_name = storage.fail_url_fetch.call_args[0]
    assert db_name == "default"
    assert "network timeout" in error_msg


@pytest.mark.asyncio
async def test_placeholder_delete_failure_logs_database_name_and_sql_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F7/F16: orphan delete failure logs database_name and SQL error code for triage."""
    import structlog.testing

    sps, storage = _make_sps(monkeypatch)
    sps.upload_file = AsyncMock(return_value={"id": "src_real", "status": "pending"})

    # Stage an OperationalError-shaped exception with a `.orig.sqlstate` to
    # verify the handler surfaces the driver-level error code.
    class _FakeOrig:
        sqlstate = "HY000"

    class _FakeDBError(Exception):
        orig = _FakeOrig()

    storage.delete_source = MagicMock(side_effect=_FakeDBError("locked"))

    long_content = "Real content that is long enough to pass the minimum check. " * 20

    with patch("chaoscypher_core.adapters.web.search.WebScraper") as mock_scraper:
        scraper_instance = mock_scraper.return_value
        scraper_instance.extract_full_content = AsyncMock(
            return_value=FetchResult(
                content=long_content,
                content_type="text/html",
                title="Example Page",
                error=None,
            )
        )

        with structlog.testing.capture_logs() as captured:
            result = await handle_fetch_url(
                data={"url": "https://example.com", "options": {}},
                source_processing_service=sps,
                metadata={"database_name": "tenant_a", "operation_type": "fetch_url"},
            )

    # Upload still succeeds — orphan placeholder is logged, not raised.
    assert result == {"id": "src_real", "status": "pending"}

    matching = [e for e in captured if e.get("event") == "url_fetch_placeholder_delete_failed"]
    assert len(matching) == 1, f"expected one delete-failure warning, got {captured}"
    event = matching[0]
    assert event["database_name"] == "tenant_a"
    assert event["sql_error_code"] == "HY000"
    assert event["error_type"] == "_FakeDBError"
    assert event["canonical_source_id"] == "src_real"
