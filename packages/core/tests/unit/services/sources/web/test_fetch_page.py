# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``fetch_page``: the one page fetch every URL import goes through.

Site import design, decision 1: the worker's ``fetch_url`` handler, the CLI's
``upload_url`` and the MCP server's URL import all call ``fetch_page``; it
wraps ``WebScraper.extract_full_content`` once and owns the content-hash rule
(sha256 of the extracted text's UTF-8 bytes, or of the raw bytes for a binary
response).
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.ports.web import FetchResult
from chaoscypher_core.services.sources.web import FetchedPage, fetch_page, upload_fetch_limits
from chaoscypher_core.settings import BatchingSettings, WebSettings


def _settings(**batching: Any) -> SimpleNamespace:
    return SimpleNamespace(web=WebSettings(), batching=BatchingSettings(**batching))


def _fetcher(result: FetchResult) -> MagicMock:
    fetcher = MagicMock()
    fetcher.extract_full_content = AsyncMock(return_value=result)
    return fetcher


@pytest.mark.asyncio
async def test_text_page_hashes_extracted_markdown() -> None:
    markdown = "# Title\n\nBody text with ünïcode."
    fetcher = _fetcher(
        FetchResult(
            content=markdown,
            content_type="text/html",
            url="https://example.com/a",
            final_url="https://example.com/b",
            title="A page",
            encoding_used="utf-8",
        )
    )

    page = await fetch_page("https://example.com/a", _settings(), fetcher=fetcher)

    assert isinstance(page, FetchedPage)
    assert page.url == "https://example.com/a"
    assert page.final_url == "https://example.com/b"
    assert page.content_type == "text/html"
    assert page.is_binary is False
    assert page.content == markdown
    assert page.data is None
    assert page.title == "A page"
    assert page.error is None
    assert page.payload == markdown.encode("utf-8")
    assert page.content_hash == hashlib.sha256(markdown.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_binary_page_hashes_raw_bytes() -> None:
    raw = b"%PDF-1.7 binary payload"
    fetcher = _fetcher(
        FetchResult(content="", content_type="application/pdf", is_binary=True, bytes=raw)
    )

    page = await fetch_page("https://example.com/doc.pdf", _settings(), fetcher=fetcher)

    assert page.is_binary is True
    assert page.data == raw
    assert page.payload == raw
    assert page.content_hash == hashlib.sha256(raw).hexdigest()
    # No redirect information from the fetcher: the requested URL stands in.
    assert page.final_url == "https://example.com/doc.pdf"


@pytest.mark.asyncio
async def test_fetch_error_is_carried_not_raised() -> None:
    fetcher = _fetcher(FetchResult(content="", content_type="", error="Failed to fetch URL"))

    page = await fetch_page("https://example.com/x", _settings(), fetcher=fetcher)

    assert page.error == "Failed to fetch URL"
    assert page.content == ""


@pytest.mark.asyncio
async def test_validation_error_propagates() -> None:
    fetcher = MagicMock()
    fetcher.extract_full_content = AsyncMock(side_effect=ValidationError("bad type", field="url"))

    with pytest.raises(ValidationError):
        await fetch_page("https://example.com/x", _settings(), fetcher=fetcher)


@pytest.mark.asyncio
async def test_default_fetcher_built_from_upload_limits_and_web_settings() -> None:
    """With no fetcher injected, the WebScraper gets the upload allowlist, cap and web settings."""
    settings = _settings(max_upload_bytes=4096, upload_content_type_allowlist=["text/html"])
    result = FetchResult(content="x" * 100, content_type="text/html")

    with patch("chaoscypher_core.adapters.web.search.WebScraper") as scraper_cls:
        scraper_cls.return_value.extract_full_content = AsyncMock(return_value=result)
        await fetch_page("https://example.com/", settings)

    scraper_cls.assert_called_once_with(
        allowlist=["text/html"], max_bytes=4096, web_settings=settings.web
    )
    scraper_cls.return_value.extract_full_content.assert_awaited_once_with(
        "https://example.com/", max_bytes=4096
    )


def test_upload_fetch_limits_tolerates_missing_batching() -> None:
    """A settings object without a batching group means no allowlist and no cap."""
    assert upload_fetch_limits(SimpleNamespace()) == ([], None)
    assert upload_fetch_limits(_settings(max_upload_bytes=10)) == (
        list(BatchingSettings().upload_content_type_allowlist),
        10,
    )


@pytest.mark.asyncio
async def test_a_markdown_page_without_a_title_is_named_by_its_first_heading() -> None:
    """A ``.md`` page (an llms.txt link, often) has no ``<title>``: its H1 names it.

    Until 2026-10-22 every Markdown page imported as "Untitled Page", so a
    site imported from its llms.txt listed one name for every page.
    """
    markdown = "Intro line\n\n# Install Ollama\n\nRun the installer.\n\n# Later heading\n"
    fetcher = _fetcher(
        FetchResult(
            content=markdown,
            content_type="text/markdown",
            url="https://docs.example.com/install.md",
            final_url="https://docs.example.com/install.md",
        )
    )

    page = await fetch_page("https://docs.example.com/install.md", _settings(), fetcher=fetcher)

    assert page.title == "Install Ollama"


@pytest.mark.asyncio
async def test_a_plain_text_page_keeps_no_title() -> None:
    fetcher = _fetcher(
        FetchResult(
            content="# not a heading in plain text",
            content_type="text/plain",
            url="https://example.com/a.txt",
            final_url="https://example.com/a.txt",
        )
    )

    page = await fetch_page("https://example.com/a.txt", _settings(), fetcher=fetcher)

    assert page.title == ""
