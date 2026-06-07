# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""URL fetcher rejects responses whose Content-Type isn't in the allowlist.

W9 (2026-05-08): The URL fetcher used to accept any Content-Type the
remote server returned, which meant a URL pointing at a PDF was fed to
trafilatura's HTML extractor (which produced empty / garbled output) or
an octet-stream fell through to UTF-8 decode and got mojibake. This
suite pins the new contract:

1. Responses outside the upload allowlist raise ``ValidationError`` so
   the user sees an actionable message instead of an empty source.
2. Binary content types (``application/pdf``) come back as
   ``FetchResult(is_binary=True, bytes=...)`` so the URL fetch handler
   can stage them for the appropriate loader.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from chaoscypher_core.adapters.web.search import FetchResult, WebScraper
from chaoscypher_core.exceptions import ValidationError


def _build_response(
    *,
    content_type: str,
    body: bytes,
    url: str = "https://example.com/",
    status_code: int = 200,
) -> httpx.Response:
    """Build an ``httpx.Response`` with the supplied headers + body."""
    return httpx.Response(
        status_code,
        request=httpx.Request("GET", url),
        headers={"content-type": content_type},
        content=body,
    )


@pytest.fixture
def mock_url_response(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Patch the redirect-validating fetcher to return a stub response.

    Tests configure the response's Content-Type and body; the helper
    bypasses the network and the URL safety check so the production
    Content-Type / charset logic in ``extract_full_content`` runs
    against deterministic input.
    """
    captured: dict[str, httpx.Response] = {}

    def configure(*, url: str, content_type: str, body: bytes) -> None:
        response = _build_response(url=url, content_type=content_type, body=body)
        captured["response"] = response

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
async def test_url_fetcher_rejects_octet_stream(mock_url_response: Any) -> None:
    mock_url_response(
        url="https://example.com/mystery.bin",
        content_type="application/octet-stream",
        body=b"\x00\x01\x02",
    )
    scraper = WebScraper(allowlist=["text/html", "text/plain", "text/markdown", "application/pdf"])
    with pytest.raises(ValidationError) as exc:
        await scraper.extract_full_content("https://example.com/mystery.bin")
    assert "content type" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_url_fetcher_routes_pdf_through_pdf_path(mock_url_response: Any) -> None:
    """A URL serving application/pdf is fetched as bytes for the PDF loader."""
    mock_url_response(
        url="https://example.com/doc.pdf",
        content_type="application/pdf",
        body=b"%PDF-1.4\n...",
    )
    scraper = WebScraper(
        allowlist=["text/html", "application/pdf"],
    )
    result = await scraper.extract_full_content("https://example.com/doc.pdf")
    assert isinstance(result, FetchResult)
    assert result.content_type == "application/pdf"
    assert result.is_binary is True
    assert result.bytes is not None
    assert result.bytes.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_url_fetcher_passes_through_when_allowlist_empty(
    mock_url_response: Any,
) -> None:
    """An empty allowlist disables the check (legacy behaviour)."""
    mock_url_response(
        url="https://example.com/anything",
        content_type="application/octet-stream",
        body=b"<html><body>Hello world this is some content</body></html>",
    )
    # Default constructor: no allowlist → no Content-Type check.
    scraper = WebScraper()
    # Should not raise.
    result = await scraper.extract_full_content("https://example.com/anything")
    assert isinstance(result, FetchResult)
    assert result.content_type == "application/octet-stream"


@pytest.mark.asyncio
async def test_url_fetcher_rejects_oversized_binary(
    mock_url_response: Any,
) -> None:
    """Binary content beyond ``max_bytes`` raises a ValidationError."""
    body = b"\x00" * 4096  # 4 KiB
    mock_url_response(
        url="https://example.com/big.pdf",
        content_type="application/pdf",
        body=body,
    )
    scraper = WebScraper(allowlist=["application/pdf"], max_bytes=1024)
    with pytest.raises(ValidationError) as exc:
        await scraper.extract_full_content("https://example.com/big.pdf")
    assert "max upload size" in str(exc.value).lower() or "exceeds" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_url_fetcher_returns_text_for_html(mock_url_response: Any) -> None:
    """HTML content comes back as decoded text with ``is_binary=False``."""
    html = b"<html><body><p>Hello, knowledge graphs!</p></body></html>"
    mock_url_response(
        url="https://example.com/page.html",
        content_type="text/html",
        body=html,
    )
    scraper = WebScraper(allowlist=["text/html"])
    result = await scraper.extract_full_content("https://example.com/page.html")
    assert isinstance(result, FetchResult)
    assert result.is_binary is False
    assert result.bytes is None
    # trafilatura should pick up the paragraph text.
    assert "Hello" in result.content


@pytest.mark.asyncio
async def test_url_fetcher_strips_charset_suffix_for_check(
    mock_url_response: Any,
) -> None:
    """``Content-Type: text/html; charset=utf-8`` matches an allowlist of ``text/html``."""
    mock_url_response(
        url="https://example.com/page",
        content_type="text/html; charset=utf-8",
        body=b"<html><body><p>charset suffix is stripped</p></body></html>",
    )
    scraper = WebScraper(allowlist=["text/html"])
    # Should not raise — the bare media type is in the allowlist.
    result = await scraper.extract_full_content("https://example.com/page")
    assert isinstance(result, FetchResult)
    assert result.content_type == "text/html"


@pytest.mark.asyncio
async def test_url_fetcher_wildcard_disables_check(mock_url_response: Any) -> None:
    """Allowlist ``['*']`` accepts everything (per BatchSettings docs)."""
    mock_url_response(
        url="https://example.com/anything",
        content_type="application/x-rare-format",
        body=b"<html><p>let it through</p></html>",
    )
    scraper = WebScraper(allowlist=["*"])
    result = await scraper.extract_full_content("https://example.com/anything")
    assert isinstance(result, FetchResult)
    assert result.content_type == "application/x-rare-format"


@pytest.mark.asyncio
async def test_url_fetch_handler_routes_binary_through_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """The fetch handler stages binary content with the correct extension.

    Regression for the wedge that fed PDFs through the markdown writer
    + UTF-8 encode path. The handler must:

    1. Write the response bytes verbatim to a staged file.
    2. Use the extension implied by the response Content-Type
       (``application/pdf`` → ``.pdf``).
    3. Forward the staged path to ``upload_file`` with no in-memory
       ``file_content`` (so the loader registry picks the PDF loader).
    """
    from chaoscypher_core.operations.sources.url_fetch_handler import handle_fetch_url

    storage = MagicMock()
    config_manager = MagicMock()
    config_manager.get_settings.return_value = MagicMock(current_database="default")

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

    sps = MagicMock()
    sps.source_manager = storage
    sps.config_manager = config_manager
    captured_kwargs: dict[str, Any] = {}

    async def fake_upload_file(**kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        return {"id": "src_new", "filename": kwargs.get("filename", "")}

    captured_bytes: dict[str, bytes] = {}

    async def fake_upload_file_with_capture(**kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        # Read the staged file *before* the handler unlinks it in the
        # ``finally`` clause around the upload.
        staged = kwargs.get("staged_file_path")
        if staged is not None:
            captured_bytes["staged"] = staged.read_bytes()
        return {"id": "src_new", "filename": kwargs.get("filename", "")}

    sps.upload_file = AsyncMock(side_effect=fake_upload_file_with_capture)

    pdf_bytes = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nbody bytes large enough to pass the 50-byte minimum content check..."

    with patch("chaoscypher_core.adapters.web.search.WebScraper") as mock_scraper_cls:
        scraper_instance = mock_scraper_cls.return_value
        scraper_instance.extract_full_content = AsyncMock(
            return_value=FetchResult(
                content="",
                content_type="application/pdf",
                is_binary=True,
                bytes=pdf_bytes,
                url="https://example.com/doc.pdf",
                title="",
                error=None,
            )
        )

        await handle_fetch_url(
            data={"url": "https://example.com/doc.pdf", "options": {}},
            source_processing_service=sps,
            metadata={"database_name": "default", "operation_type": "fetch_url"},
            task_id="tsk_pdf",
        )

    # Filename derived from the URL must keep the PDF extension so the
    # loader registry routes through the PDF loader.
    assert captured_kwargs["filename"].endswith(".pdf")
    # Binary contents must travel via staged_file_path, not file_content.
    assert captured_kwargs.get("file_content") is None
    staged = captured_kwargs.get("staged_file_path")
    assert staged is not None
    # The staged file (now unlinked) held the original bytes verbatim —
    # no UTF-8 round-trip through ``content.encode``.
    assert captured_bytes["staged"].startswith(b"%PDF-")
    assert staged.suffix == ".pdf"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_type", "expected_ext", "body_text"),
    [
        # JSON URL must stage as .json so the JSONL/JSON loader picks it
        # up — not the markdown loader.
        (
            "application/json",
            ".json",
            '{"records": [' + ", ".join([f'{{"i": {i}}}' for i in range(20)]) + "]}",
        ),
        # CSV URL must stage as .csv so the dialect-sniffing CSV loader
        # picks it up.
        (
            "text/csv",
            ".csv",
            "a,b,c\n" + "\n".join([f"{i},{i + 1},{i + 2}" for i in range(20)]),
        ),
        # text/plain → .txt (no markdown extraction).
        (
            "text/plain",
            ".txt",
            "plain text body that is long enough to pass the 50-byte minimum content check easily.",
        ),
        # text/xml / application/xml → .xml.
        (
            "application/xml",
            ".xml",
            "<root>" + ("<item>x</item>" * 20) + "</root>",
        ),
        # text/html stays on .md because trafilatura emits markdown.
        (
            "text/html",
            ".md",
            "<html><body>"
            + ("<p>Hello, knowledge graphs! Plenty of body text. </p>" * 5)
            + "</body></html>",
        ),
    ],
)
async def test_textual_url_imports_use_correct_extension(
    monkeypatch: pytest.MonkeyPatch,
    content_type: str,
    expected_ext: str,
    body_text: str,
) -> None:
    """Textual URL imports stage with the Content-Type-appropriate extension.

    Regression for the wedge that staged every textual fetch as ``.md``
    regardless of Content-Type — feeding JSON / CSV / plain-text URLs
    through the markdown loader and bypassing the W6/W7 format-aware
    loaders (JSONL line-by-line, CSV dialect sniffing).
    """
    from chaoscypher_core.operations.sources.url_fetch_handler import handle_fetch_url

    storage = MagicMock()
    config_manager = MagicMock()
    config_manager.get_settings.return_value = MagicMock(current_database="default")

    settings = MagicMock()
    settings.batching.max_upload_bytes = 1024 * 1024
    settings.batching.upload_content_type_allowlist = [
        "text/html",
        "text/plain",
        "text/markdown",
        "text/csv",
        "text/xml",
        "application/xml",
        "application/json",
        "application/xhtml+xml",
    ]
    monkeypatch.setattr(
        "chaoscypher_core.operations.sources.url_fetch_handler.get_settings",
        lambda: settings,
    )

    sps = MagicMock()
    sps.source_manager = storage
    sps.config_manager = config_manager
    captured_kwargs: dict[str, Any] = {}
    captured_bytes: dict[str, bytes] = {}

    async def fake_upload_file(**kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        staged = kwargs.get("staged_file_path")
        if staged is not None:
            captured_bytes["staged"] = staged.read_bytes()
        return {"id": "src_new", "filename": kwargs.get("filename", "")}

    sps.upload_file = AsyncMock(side_effect=fake_upload_file)

    with patch("chaoscypher_core.adapters.web.search.WebScraper") as mock_scraper_cls:
        scraper_instance = mock_scraper_cls.return_value
        scraper_instance.extract_full_content = AsyncMock(
            return_value=FetchResult(
                content=body_text,
                content_type=content_type,
                is_binary=False,
                bytes=None,
                url="https://example.com/resource",
                title="Resource",
                error=None,
            )
        )

        await handle_fetch_url(
            data={"url": "https://example.com/resource", "options": {}},
            source_processing_service=sps,
            metadata={"database_name": "default", "operation_type": "fetch_url"},
            task_id=f"tsk_{expected_ext.lstrip('.')}",
        )

    # Filename and staged path both carry the Content-Type-appropriate
    # extension so the loader registry picks the right loader downstream.
    assert captured_kwargs["filename"].endswith(expected_ext), (
        f"filename {captured_kwargs['filename']!r} does not end with {expected_ext}"
    )
    staged = captured_kwargs.get("staged_file_path")
    assert staged is not None
    assert staged.suffix == expected_ext, (
        f"staged path suffix {staged.suffix!r} does not match {expected_ext}"
    )
    # The staged file held the encoded body verbatim.
    assert captured_bytes["staged"] == body_text.encode("utf-8")


def test_textual_extension_map_covers_expected_content_types() -> None:
    """Direct unit check on the helper so a future reshuffle gets caught."""
    from chaoscypher_core.operations.sources.url_fetch_handler import (
        _textual_extension_for,
    )

    assert _textual_extension_for("application/json") == ".json"
    assert _textual_extension_for("text/csv") == ".csv"
    assert _textual_extension_for("text/plain") == ".txt"
    assert _textual_extension_for("text/html") == ".md"
    assert _textual_extension_for("text/markdown") == ".md"
    assert _textual_extension_for("text/xml") == ".xml"
    assert _textual_extension_for("application/xml") == ".xml"
    assert _textual_extension_for("application/xhtml+xml") == ".html"
    # Case-insensitive match.
    assert _textual_extension_for("Application/JSON") == ".json"
    # Unknown textual type → markdown fallback (legacy default).
    assert _textual_extension_for("text/x-rare-format") == ".md"
