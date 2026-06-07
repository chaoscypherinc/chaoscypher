# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: max_bytes overflow returns an error dict, not a magic-string sentinel."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.adapters.web.search import WebScraper
from chaoscypher_core.exceptions import MaxBytesExceeded


@pytest.mark.asyncio
async def test_max_bytes_returns_error_when_exceeded() -> None:
    scraper = WebScraper()
    scraper._fetch_with_redirect_validation_capped = AsyncMock(
        side_effect=MaxBytesExceeded("Content exceeded max_bytes=100")
    )

    result = await scraper.extract_full_content("https://example.com/", max_bytes=100)

    assert result.error
    assert "max_bytes" in result.error.lower() or "100" in result.error
    assert result.content == ""
    assert result.url == "https://example.com/"
