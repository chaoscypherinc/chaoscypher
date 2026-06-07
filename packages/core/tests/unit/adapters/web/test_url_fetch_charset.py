# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""URL fetcher decodes per response charset, not hardcoded UTF-8.

W9 (2026-05-08): The previous implementation called
``raw.decode("utf-8", errors="replace")`` which silently turned every
non-UTF-8 page into mojibake. This regression suite pins the charset
contract:

* ``Content-Type: text/html; charset=iso-8859-1`` decodes via Latin-1.
* ``Content-Type: text/plain; charset=cp1252`` decodes via cp1252.
* When the response advertises a charset that fails strict decode
  (declared utf-8 but the bytes aren't), we fall back to
  ``detect_encoding`` rather than emitting U+FFFD replacement chars.
* Missing charsets default to the UTF-8 attempt and only fall back if
  it cannot decode strictly.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from chaoscypher_core.adapters.web.search import FetchResult, WebScraper


# Smart-quote codepoints that the tests below intentionally exercise
# under cp1252. Spelled with ``\u`` escapes so the test source stays
# pure ASCII; the per-line ``noqa`` keeps ruff's RUF001 quiet on the
# right-single-quote (codepoint 0x2019, the cp1252 apostrophe) which
# is the literal character whose decode this file proves works.
LEFT_DOUBLE_QUOTE = "“"
RIGHT_DOUBLE_QUOTE = "”"
EM_DASH = "—"
RIGHT_SINGLE_QUOTE = "’"  # noqa: RUF001 - intentional: this test verifies its decoding


def _build_response(
    *,
    content_type: str,
    body: bytes,
    url: str = "https://example.com/",
    status_code: int = 200,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        request=httpx.Request("GET", url),
        headers={"content-type": content_type},
        content=body,
    )


@pytest.fixture
def mock_url_response(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Patch the redirect-validating fetcher to return a stub response."""
    captured: dict[str, httpx.Response] = {}

    def configure(*, url: str, content_type: str, body: bytes) -> None:
        captured["response"] = _build_response(url=url, content_type=content_type, body=body)

        async def fake_capped(
            self: WebScraper, _fetch_url: str, _max_bytes: int | None
        ) -> httpx.Response:
            return captured["response"]

        monkeypatch.setattr(
            WebScraper,
            "_fetch_with_redirect_validation_capped",
            fake_capped,
        )

    return configure


@pytest.mark.asyncio
async def test_url_fetcher_uses_iso_8859_1_when_response_says_so(
    mock_url_response: Any,
) -> None:
    body = "café résumé naïve über".encode("iso-8859-1")
    mock_url_response(
        url="https://example.com/old.html",
        content_type="text/html; charset=iso-8859-1",
        body=b"<html><body><p>" + body + b"</p></body></html>",
    )
    scraper = WebScraper(allowlist=["text/html"])
    result = await scraper.extract_full_content("https://example.com/old.html")
    assert isinstance(result, FetchResult)
    # The accented characters survived (no mojibake / no replacement).
    assert "café" in result.content
    assert "résumé" in result.content
    assert "�" not in result.content


@pytest.mark.asyncio
async def test_url_fetcher_uses_cp1252_when_response_says_so(
    mock_url_response: Any,
) -> None:
    """Smart-quote bytes (0x91-0x94) decode correctly under cp1252."""
    text = (
        f"He said {LEFT_DOUBLE_QUOTE}hello{RIGHT_DOUBLE_QUOTE} {EM_DASH} "
        f"it{RIGHT_SINGLE_QUOTE}s working"
    )
    text_with_smart_quotes = text.encode("cp1252")
    mock_url_response(
        url="https://example.com/notepad.txt",
        content_type="text/plain; charset=cp1252",
        body=text_with_smart_quotes,
    )
    scraper = WebScraper(allowlist=["text/plain"])
    result = await scraper.extract_full_content("https://example.com/notepad.txt")
    assert isinstance(result, FetchResult)
    assert LEFT_DOUBLE_QUOTE in result.content
    assert RIGHT_DOUBLE_QUOTE in result.content
    assert EM_DASH in result.content
    assert RIGHT_SINGLE_QUOTE in result.content
    assert "�" not in result.content  # No U+FFFD replacement char.


@pytest.mark.asyncio
async def test_url_fetcher_falls_back_when_declared_charset_is_wrong(
    mock_url_response: Any,
) -> None:
    """Server declares utf-8 but body is cp1252: detect_encoding rescues.

    Regression: a page ``Content-Type: text/html; charset=utf-8`` whose
    body actually carries cp1252 smart quotes used to come back as
    mojibake under the strict UTF-8 decode. The fallback to
    ``detect_encoding`` keeps the characters intact.
    """
    inner = f"He said {LEFT_DOUBLE_QUOTE}hi{RIGHT_DOUBLE_QUOTE} {EM_DASH} cp1252 hidden in utf-8"
    cp1252_body = b"<html><body><p>" + inner.encode("cp1252") + b"</p></body></html>"
    mock_url_response(
        url="https://example.com/wrong-charset.html",
        content_type="text/html; charset=utf-8",
        body=cp1252_body,
    )
    scraper = WebScraper(allowlist=["text/html"])
    result = await scraper.extract_full_content("https://example.com/wrong-charset.html")
    assert isinstance(result, FetchResult)
    assert f"{LEFT_DOUBLE_QUOTE}hi{RIGHT_DOUBLE_QUOTE}" in result.content
    assert EM_DASH in result.content
    assert "�" not in result.content


@pytest.mark.asyncio
async def test_url_fetcher_handles_missing_charset(mock_url_response: Any) -> None:
    """Bare ``Content-Type: text/html`` defaults to UTF-8."""
    body = b"<html><body><p>Plain ASCII body, no charset header.</p></body></html>"
    mock_url_response(
        url="https://example.com/no-charset.html",
        content_type="text/html",
        body=body,
    )
    scraper = WebScraper(allowlist=["text/html"])
    result = await scraper.extract_full_content("https://example.com/no-charset.html")
    assert isinstance(result, FetchResult)
    assert "Plain ASCII body" in result.content
    assert result.encoding_used == "utf-8"
