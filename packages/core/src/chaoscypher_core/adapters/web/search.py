# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Web content extraction using trafilatura.

Provides WebScraper for extracting clean content from web pages.
Used by source importers and MCP document processing for URL imports.

Example:
    scraper = WebScraper()
    result = await scraper.extract_full_content("https://example.com/article")

``extract_full_content`` returns a ``FetchResult`` dataclass instead
of an untyped dict so callers can distinguish text vs binary
responses, see the resolved Content-Type, and route binary content
(PDF, images) through the staging pipeline. The scraper also
validates the response Content-Type against the upload allowlist and
honors the response charset rather than hardcoding UTF-8 with
replacement.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import structlog
import trafilatura

from chaoscypher_core.exceptions import MaxBytesExceeded, ValidationError
from chaoscypher_core.settings import WebSettings
from chaoscypher_core.utils.encoding import detect_encoding
from chaoscypher_core.utils.url_safety import validate_url_safety


logger = structlog.get_logger(__name__)

_client: httpx.AsyncClient | None = None


def _get_client(timeout_seconds: float) -> httpx.AsyncClient:
    """Return the module-level cached httpx client, creating it on first call.

    Lazy init avoids requiring an event loop at import time. No explicit
    max_connections limit is set — no ``WebSettings`` key exists for it, so
    we fall back to the httpx default (100 keep-alive connections). The
    ``follow_redirects=False`` default is NOT set here because callers
    need per-request control; they call ``client.get()`` / ``client.stream()``
    directly with no per-call timeout override (timeout comes from the
    caller's injected ``WebSettings``).

    Args:
        timeout_seconds: HTTP timeout, sourced from the scraper's injected
            ``WebSettings.fetch_timeout_seconds``. Because the client is
            cached at module scope, the timeout of the *first* caller wins
            for the process lifetime — matching the prior single-singleton
            behaviour.
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=False,
        )
    return _client


# Content types that ``trafilatura`` can usefully process. Anything
# outside this set is treated as binary and surfaced via
# ``FetchResult.bytes`` so the upload pipeline can dispatch to the
# right loader (PDF, image, etc.).
_TEXTUAL_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/json",
        "application/xml",
        "application/xhtml+xml",
        "application/x-yaml",
        "application/yaml",
        "application/javascript",
    }
)


@dataclass
class FetchResult:
    """Result of fetching a URL.

    Attributes:
        content: Decoded text content. Empty string for binary fetches.
        content_type: Bare media type (e.g. ``application/pdf``), with
            any ``charset=`` / ``boundary=`` parameters stripped.
        is_binary: True when the response was treated as binary
            (Content-Type outside the textual set). The ``bytes``
            field carries the raw payload in that case.
        bytes: Raw response body for binary fetches. ``None`` for
            textual fetches.
        url: The originally requested URL (post-redirect resolution
            happens at the HTTP layer; the URL the caller asked about
            is preserved here for logging / row metadata).
        title: Page title extracted by trafilatura. Empty for binary.
        author: Author metadata extracted by trafilatura. ``None``
            when not available or when the response is binary.
        date: Publication date extracted by trafilatura. ``None`` when
            not available or when the response is binary.
        encoding_used: The encoding label used to decode the body
            (e.g. ``"utf-8"``, ``"cp1252"``). ``None`` for binary
            fetches and for the legacy short-circuit error paths.
        error: Human-readable error message when the fetch failed
            cleanly (network error, byte cap exceeded, empty
            extraction). Allowlist / size violations raise
            ``ValidationError`` instead so the user gets a 400.
    """

    content: str
    content_type: str
    is_binary: bool = False
    bytes: bytes | None = None
    url: str = ""
    title: str = ""
    author: str | None = None
    date: str | None = None
    encoding_used: str | None = None
    error: str | None = None


class WebScraper:
    """Extract clean content from web pages using trafilatura.

    Trafilatura is a battle-tested library for extracting main content,
    removing boilerplate, and handling metadata from web pages.

    Args:
        allowlist: Allowed Content-Type media types. If empty (the
            default for backwards-compat), no Content-Type check is
            performed. Pass ``["*"]`` to disable the check explicitly.
            Operators usually pass
            ``settings.batching.upload_content_type_allowlist`` so URL
            imports follow the same allowlist as file uploads.
        max_bytes: Hard cap on the binary payload size (in bytes).
            ``None`` (default) leaves binary fetches uncapped — the
            byte cap on the textual fetch path comes from the
            per-call ``max_bytes`` argument to ``extract_full_content``.
        web_settings: HTTP / web-fetch behaviour (timeout, redirect
            budget). ``None`` (default) falls back to ``WebSettings()``
            class defaults. Creators that have an ``EngineSettings`` in
            scope should pass ``engine_settings.web`` so the fetch
            timeout and redirect budget reflect operator configuration.

    """

    def __init__(
        self,
        *,
        allowlist: list[str] | None = None,
        max_bytes: int | None = None,
        web_settings: WebSettings | None = None,
    ) -> None:
        """Initialize the scraper with an optional Content-Type allowlist.

        Args:
            allowlist: Allowed media types. Empty list disables the
                check. ``["*"]`` also disables the check explicitly.
            max_bytes: Maximum payload size for binary fetches.
            web_settings: Injected web-fetch configuration. ``None``
                resolves to ``WebSettings()`` class defaults.
        """
        # Normalise the allowlist to lowercase media types so the
        # comparison in ``_check_content_type`` is case-insensitive.
        self._allowlist = [item.lower().strip() for item in (allowlist or [])]
        self._max_bytes = max_bytes
        self._web_settings = web_settings if web_settings is not None else WebSettings()

    def _check_content_type(self, content_type: str) -> None:
        """Raise ``ValidationError`` when *content_type* is not allowed.

        The allowlist comparison is performed against the bare media
        type (``text/html``) — any ``charset=`` / ``boundary=``
        parameters are stripped before the lookup. An empty allowlist
        or one containing the wildcard ``*`` disables the check.
        """
        if not self._allowlist or "*" in self._allowlist:
            return
        if content_type in self._allowlist:
            return
        msg = (
            f"Content type '{content_type}' is not in the upload allowlist. "
            f"The URL must serve one of: "
            f"{', '.join(sorted(self._allowlist))}."
        )
        raise ValidationError(msg, field="url")

    @staticmethod
    def _parse_content_type(header_value: str) -> tuple[str, str | None]:
        """Return ``(media_type, charset_or_none)`` from a Content-Type header.

        ``Content-Type: text/html; charset=ISO-8859-1`` →
        ``("text/html", "iso-8859-1")``. Multiple ``;``-separated
        parameters are tolerated; only ``charset`` is extracted. The
        returned media type is lowercased.
        """
        parts = [p.strip() for p in header_value.split(";") if p.strip()]
        if not parts:
            return ("", None)
        media_type = parts[0].lower()
        charset: str | None = None
        for part in parts[1:]:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key.strip().lower() == "charset":
                # Strip optional surrounding quotes per RFC 7231.
                charset = value.strip().strip('"').strip("'").lower()
                if charset:
                    break
        return (media_type, charset)

    @staticmethod
    def _decode_with_charset(raw: bytes, charset: str | None) -> tuple[str, str]:
        """Decode *raw* using *charset*; fall back to ``detect_encoding``.

        Strict decode is attempted first so we surface bad data instead
        of silently emitting U+FFFD. On ``UnicodeDecodeError`` /
        ``LookupError`` (unknown codec name) we write the bytes to a
        tempfile and ask :func:`detect_encoding` for a verdict — this
        catches servers that mislabel the charset in the header. The
        return value is ``(text, encoding_used)`` where
        ``encoding_used`` records which codec actually succeeded so
        callers / quality counters can surface it.
        """
        if charset:
            try:
                return (raw.decode(charset), charset)
            except UnicodeDecodeError, LookupError:
                logger.info(
                    "url_fetch_charset_mismatch",
                    declared_charset=charset,
                    body_bytes=len(raw),
                )
        # Either no charset or strict decode failed — defer to the
        # shared detector. ``detect_encoding`` reads the file from
        # disk; tempfile is cheap (sub-ms for typical web pages). It
        # returns ``(encoding_used, decoded_text)`` so we swap the
        # tuple to match this helper's contract.
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)
        try:
            encoding_used, text, _ = detect_encoding(tmp_path)
            return (text, encoding_used)
        finally:
            tmp_path.unlink(missing_ok=True)

    async def extract_content(self, url: str, max_length: int = 5000) -> dict[str, Any]:
        """Extract main content from a web page.

        Args:
            url: URL to scrape
            max_length: Maximum content length to return

        Returns:
            Dict with 'url', 'title', 'content', 'error' keys

        """
        try:
            if not validate_url_safety(url, strict=True):
                return {
                    "url": url,
                    "title": "",
                    "content": "",
                    "error": "URL blocked by security policy",
                }

            # Fetch with httpx (no automatic redirects) to prevent SSRF via
            # redirect to internal/metadata endpoints. Manually follow redirects
            # only after validating each target URL.
            downloaded = await self._fetch_with_redirect_validation(url)
            if not downloaded:
                return {
                    "url": url,
                    "title": "",
                    "content": "",
                    "error": "Failed to fetch URL",
                }

            # Extract main content
            content = await asyncio.to_thread(
                trafilatura.extract,
                downloaded,
                include_comments=False,
                include_tables=True,
                deduplicate=True,
            )

            # Extract metadata for title
            metadata = await asyncio.to_thread(trafilatura.extract_metadata, downloaded)
            title = metadata.title if metadata and metadata.title else ""

            # Handle empty content
            if not content:
                return {
                    "url": url,
                    "title": title,
                    "content": "",
                    "error": "Could not extract content",
                }

            # Truncate if too long
            if len(content) > max_length:
                content = content[:max_length] + "..."

            return {"url": url, "title": title, "content": content, "error": None}

        except Exception as e:
            logger.warning(
                "content_extraction_failed",
                url=url,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {"url": url, "title": "", "content": "", "error": "Failed to extract content"}

    async def _fetch_with_redirect_validation(self, url: str) -> str | None:
        """Fetch URL content with redirect validation to prevent SSRF.

        Uses httpx with redirects disabled, manually following each redirect
        after validating the target URL against the safety policy.

        Args:
            url: URL to fetch.

        Returns:
            Downloaded HTML content, or None on failure.

        """
        max_redirects = self._web_settings.max_redirects
        current_url = url
        client = _get_client(self._web_settings.fetch_timeout_seconds)
        for _ in range(max_redirects):
            if not validate_url_safety(current_url, strict=True):
                logger.warning("redirect_blocked_by_safety_policy", url=current_url)
                return None
            response = await client.get(current_url)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return None
                current_url = str(response.url.join(location))
                continue
            if response.status_code >= 400:
                logger.warning(
                    "url_fetch_http_error",
                    url=current_url,
                    status_code=response.status_code,
                )
                return None
            return response.text
        logger.warning("too_many_redirects", url=url)
        return None

    async def _fetch_with_redirect_validation_capped(  # noqa: PLR0911
        self, url: str, max_bytes: int | None
    ) -> httpx.Response | None:
        """Fetch URL with redirect validation and an optional byte cap.

        Streams the response body when *max_bytes* is set so we can abort
        mid-download without reading the entire page into memory.  When
        *max_bytes* is ``None`` the behaviour is identical to
        :meth:`_fetch_with_redirect_validation` (full body, no cap).

        Args:
            url: URL to fetch.
            max_bytes: Maximum raw bytes to accumulate.  ``None`` = unlimited.

        Returns:
            The completed ``httpx.Response`` on success (with ``.content``
            and ``.headers`` populated), or ``None`` on any failure
            short-circuit (URL safety violation, too many redirects,
            HTTP 4xx/5xx, ...).

        Raises:
            MaxBytesExceeded: When the accumulated response body exceeds
                *max_bytes*.

        """
        max_redirects = self._web_settings.max_redirects
        current_url = url
        client = _get_client(self._web_settings.fetch_timeout_seconds)
        for _ in range(max_redirects):
            if not validate_url_safety(current_url, strict=True):
                logger.warning("redirect_blocked_by_safety_policy", url=current_url)
                return None
            if max_bytes is None:
                # Legacy path: single GET, full body.
                response = await client.get(current_url)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        return None
                    current_url = str(response.url.join(location))
                    continue
                if response.status_code >= 400:
                    logger.warning(
                        "url_fetch_http_error",
                        url=current_url,
                        status_code=response.status_code,
                    )
                    return None
                return response
            # Capped streaming path.
            async with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        return None
                    current_url = str(response.url.join(location))
                    continue
                if response.status_code >= 400:
                    logger.warning(
                        "url_fetch_http_error",
                        url=current_url,
                        status_code=response.status_code,
                    )
                    return None
                accumulated = bytearray()
                async for chunk in response.aiter_bytes():
                    accumulated.extend(chunk)
                    if len(accumulated) > max_bytes:
                        msg = f"Content exceeded max_bytes={max_bytes}"
                        raise MaxBytesExceeded(msg)
                # Materialise the body so callers can inspect
                # ``.content`` / ``.headers`` outside the streaming
                # context manager. ``response._content`` is the
                # documented httpx mechanism for surfacing a streamed
                # body as ``response.content``.
                response._content = bytes(accumulated)  # noqa: SLF001 - documented httpx idiom for materialising a streamed body
                return response
        logger.warning("too_many_redirects", url=url)
        return None

    async def extract_full_content(  # noqa: PLR0911
        self,
        url: str,
        *,
        max_bytes: int | None = None,
    ) -> FetchResult:
        """Fetch a URL and return a typed :class:`FetchResult`.

        Behaviour:

        * The response Content-Type is validated against the configured
          allowlist (a no-op when the allowlist is empty / ``["*"]``).
          A mismatch raises ``ValidationError`` so the user sees a 400
          instead of an empty source.
        * Textual responses are decoded using the response's declared
          charset; if the declared charset can't decode the body
          strictly, :func:`detect_encoding` is used as a fallback.
          ``text/html`` runs through trafilatura for boilerplate
          stripping; other textual types are returned verbatim.
        * Binary responses (PDF, images, ...) come back with
          ``is_binary=True`` and ``bytes`` populated so the URL fetch
          handler can stage them for the appropriate loader.

        Args:
            url: URL to scrape.
            max_bytes: Maximum response body size to accumulate. ``None``
                = unlimited (legacy default).

        Returns:
            ``FetchResult``. Network failures and missing content are
            surfaced via the ``error`` field; allowlist /
            ``max_bytes`` violations raise ``ValidationError``.
        """
        try:
            if not validate_url_safety(url, strict=True):
                return FetchResult(
                    content="",
                    content_type="",
                    url=url,
                    error="URL blocked by security policy",
                )

            try:
                response = await self._fetch_with_redirect_validation_capped(url, max_bytes)
            except MaxBytesExceeded as exc:
                return FetchResult(
                    content="",
                    content_type="",
                    url=url,
                    error=str(exc),
                )
            if response is None:
                return FetchResult(
                    content="",
                    content_type="",
                    url=url,
                    error="Failed to fetch URL",
                )

            content_type_header = response.headers.get("content-type", "")
            media_type, charset = self._parse_content_type(content_type_header)

            # Validate against the allowlist before doing any decode.
            self._check_content_type(media_type)

            raw = response.content

            # Binary route: anything outside the textual set is staged
            # as bytes for the upload pipeline.
            is_textual = media_type.startswith("text/") or media_type in _TEXTUAL_CONTENT_TYPES
            if not is_textual:
                if self._max_bytes is not None and len(raw) > self._max_bytes:
                    msg = (
                        f"URL response exceeds max upload size "
                        f"({len(raw)} bytes > {self._max_bytes})."
                    )
                    raise ValidationError(msg, field="url")
                return FetchResult(
                    content="",
                    content_type=media_type,
                    is_binary=True,
                    bytes=raw,
                    url=url,
                )

            # Text route: honor the response charset, fall back to
            # ``detect_encoding`` when the declared charset is wrong
            # or absent.
            text, encoding_used = self._decode_with_charset(raw, charset)

            if media_type == "text/html":
                # Run trafilatura against the decoded text so the
                # extractor sees the right characters even when the
                # remote server mislabels its charset.
                extracted = await asyncio.to_thread(
                    trafilatura.extract,
                    text,
                    output_format="markdown",
                    include_comments=False,
                    include_tables=True,
                    include_images=True,
                    include_links=True,
                    include_formatting=True,
                    deduplicate=True,
                )
                metadata = await asyncio.to_thread(trafilatura.extract_metadata, text)
                title = metadata.title if metadata and metadata.title else ""
                author = metadata.author if metadata and metadata.author else None
                date = metadata.date if metadata and metadata.date else None

                if not extracted:
                    return FetchResult(
                        content="",
                        content_type=media_type,
                        url=url,
                        title=title,
                        author=author,
                        date=date,
                        encoding_used=encoding_used,
                        error="Could not extract content",
                    )

                return FetchResult(
                    content=extracted,
                    content_type=media_type,
                    url=url,
                    title=title,
                    author=author,
                    date=date,
                    encoding_used=encoding_used,
                )

            # Non-HTML textual content (plain, markdown, json, xml...)
            # is returned verbatim — there is no boilerplate to strip
            # and trafilatura would mangle the structure.
            return FetchResult(
                content=text,
                content_type=media_type,
                url=url,
                encoding_used=encoding_used,
            )

        except ValidationError:
            # Validation errors propagate to the caller (typically the
            # URL fetch handler) which maps them to a row-level
            # failure. They must NOT be swallowed by the broad
            # ``except Exception`` below.
            raise
        except Exception as e:
            logger.warning(
                "full_content_extraction_failed",
                url=url,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return FetchResult(
                content="",
                content_type="",
                url=url,
                error="Failed to extract content",
            )
