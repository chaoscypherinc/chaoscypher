# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""One page fetch: the single path every URL import takes to the web.

``fetch_page`` wraps ``WebScraper.extract_full_content`` once (site import
design, decision 1). The worker's ``fetch_url`` handler, the CLI's
``upload_url`` and the MCP server's URL import call it, and the site import
calls it per page, so the fetcher's construction (upload allowlist, byte cap,
web settings) and the content-hash rule live here and nowhere else.

Content hash rule: sha256 of the extracted text's UTF-8 bytes for a textual
response (trafilatura Markdown for HTML, the body verbatim for other textual
types), or of the raw body for a binary one. It is the value
``SourceRow.content_hash`` stores, so dedup and the site refresh's
"unchanged page" test compare like with like.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.services.sources.web.site_parsers import MARKDOWN_TYPES, markdown_title


if TYPE_CHECKING:
    from chaoscypher_core.ports.web import WebFetcherPort
    from chaoscypher_core.settings import WebSettings


logger = structlog.get_logger(__name__)


# Fewest characters of extracted text (or bytes of a binary body) a URL import
# accepts as a real page; anything shorter is a JavaScript shell or an empty
# response. Shared by the worker handler and the CLI.
MIN_PAGE_CONTENT_CHARS = 50


@dataclass(frozen=True)
class FetchedPage:
    """One fetched page, ready to stage as a source.

    Attributes:
        url: The URL that was requested.
        final_url: The URL after redirects (the requested URL when the
            fetcher reported none).
        content_type: Bare media type of the response (``text/html``,
            ``application/pdf``, ...); empty when the fetch failed early.
        is_binary: True for a binary response; ``data`` holds the body.
        content: Extracted text: Markdown for HTML, the body verbatim for
            other textual types, empty for binary responses.
        data: Raw body of a binary response; ``None`` for textual ones.
        title: Page title as extracted (unsanitised; empty when absent).
        author: Author metadata, when the page carried any.
        date: Publication date metadata, when the page carried any.
        encoding_used: Codec the text was decoded with (textual responses).
        error: Why the fetch failed (network error, byte cap, blocked
            address, nothing extractable); ``None`` on success. Allowlist and
            size violations raise ``ValidationError`` instead.
    """

    url: str
    final_url: str
    content_type: str
    is_binary: bool
    content: str
    data: bytes | None
    title: str
    author: str | None = None
    date: str | None = None
    encoding_used: str | None = None
    error: str | None = None

    @cached_property
    def payload(self) -> bytes:
        """The bytes a source stages: the raw body, or the extracted text as UTF-8."""
        if self.is_binary:
            return self.data or b""
        return (self.content or "").encode("utf-8")

    @cached_property
    def content_hash(self) -> str:
        """sha256 hex digest of :attr:`payload` (the ``SourceRow.content_hash`` rule)."""
        return hashlib.sha256(self.payload).hexdigest()


_TITLE_FALLBACK = "Untitled Page"
_STEM_FALLBACK = "web_import"
# A fetched page's <title> is remote-controlled; it is persisted as
# ``SourceRow.title`` and later interpolated into the chat system prompt's
# ``<source_list>`` fence. The authoritative guard lives at that interpolation
# point (``streaming/chat/messages.py:_sanitize_source_title``); stripping the
# fence-significant (``<``/``>``) and control characters here too means the
# stored title never carries them.
_TITLE_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_TITLE_FENCE_CHARS_RE = re.compile(r"[<>]+")


@dataclass(frozen=True)
class PageNames:
    """What a fetched page is called once imported.

    Attributes:
        title: The title stored on the source row (control and ``<``/``>``
            characters removed, whitespace collapsed).
        stem: The filesystem-safe filename stem (no extension; the caller
            adds the one its content type calls for).
    """

    title: str
    stem: str


def _max_filename_length(settings: object) -> int:
    """``settings.paths.max_filename_length``, or the setting's default when absent."""
    from chaoscypher_core.settings import PathSettings

    value = getattr(getattr(settings, "paths", None), "max_filename_length", None)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    default: int = PathSettings.model_fields["max_filename_length"].default
    return default


def page_names(
    title: str | None, settings: object, *, fallback_title: str = _TITLE_FALLBACK
) -> PageNames:
    """Name a fetched page: the one title-to-filename rule every URL import shares.

    The worker's ``fetch_url`` handler, the CLI's ``upload_url``, the MCP
    server's URL import and the site import all call this, so one page gets
    one title and one filename whichever surface imported it.

    Args:
        title: The page's title as extracted (may be empty or ``None``).
        settings: Anything with a ``paths`` group; the stem is capped at
            ``paths.max_filename_length`` (the setting's default when absent).
        fallback_title: The title used when the page has none, or none
            survives cleaning.

    Returns:
        The stored title and the filename stem (``web_import`` when no word
        character survives).
    """
    cleaned = _TITLE_CONTROL_CHARS_RE.sub(" ", title or "")
    cleaned = re.sub(r"\s+", " ", _TITLE_FENCE_CHARS_RE.sub("", cleaned)).strip()
    stored = cleaned or fallback_title
    stem = re.sub(r"\s+", "_", re.sub(r"[^\w\s-]", "", stored).strip())
    return PageNames(title=stored, stem=stem[: _max_filename_length(settings)] or _STEM_FALLBACK)


def upload_fetch_limits(settings: object) -> tuple[list[str], int | None]:
    """Return the ``(content-type allowlist, byte cap)`` a URL import fetches under.

    URL imports follow the file-upload contract: the same Content-Type
    allowlist and ``max_upload_bytes`` cap (``settings.batching``). Accepts the
    app ``Settings``, ``EngineSettings`` or any object with a ``batching``
    group; a missing group (or a non-integer cap) means no allowlist and no cap.

    Args:
        settings: An object carrying the ``batching`` settings group.

    Returns:
        The allowlist (possibly empty: no check) and the cap (``None``: none).
    """
    batching = getattr(settings, "batching", None)
    allowlist = list(getattr(batching, "upload_content_type_allowlist", []) or [])
    max_bytes_raw = getattr(batching, "max_upload_bytes", None)
    max_bytes = int(max_bytes_raw) if isinstance(max_bytes_raw, int) else None
    return allowlist, max_bytes


def build_web_fetcher(
    *,
    allowlist: Sequence[str],
    max_bytes: int | None,
    web_settings: WebSettings | None,
) -> WebFetcherPort:
    """Build the default fetcher: a ``WebScraper`` (lazy fallback, CC012 allowlisted).

    Services take a ``WebFetcherPort``; when the caller injects none, this
    builds the production adapter. It is the one place in ``services`` that
    names the web adapter.

    Args:
        allowlist: Allowed response media types (empty: no check).
        max_bytes: Body cap for binary responses (``None``: none).
        web_settings: Timeout, redirect budget and User-Agent
            (``None``: ``WebSettings()`` defaults).

    Returns:
        A ready ``WebScraper``.
    """
    from chaoscypher_core.adapters.web.search import WebScraper

    return WebScraper(allowlist=list(allowlist), max_bytes=max_bytes, web_settings=web_settings)


async def fetch_page(
    url: str,
    settings: object,
    *,
    fetcher: WebFetcherPort | None = None,
) -> FetchedPage:
    """Fetch *url* once through the hardened web fetcher and describe the result.

    The fetcher vets the address with the strict SSRF policy, dials the
    pinned IP, re-checks every redirect hop, caps the body at the upload
    limit, validates the Content-Type against the upload allowlist and sends
    ``WebSettings.user_agent``.

    Args:
        url: The page to fetch.
        settings: App ``Settings`` or ``EngineSettings`` (anything with
            ``web`` and ``batching`` groups); see :func:`upload_fetch_limits`.
        fetcher: The fetcher to use; ``None`` builds a ``WebScraper`` from
            *settings*.

    Returns:
        A :class:`FetchedPage`. A fetch that failed cleanly carries the
        reason in ``error`` rather than raising.

    Raises:
        ValidationError: The response's Content-Type is not in the upload
            allowlist, or a binary body exceeds the upload cap.
    """
    allowlist, max_bytes = upload_fetch_limits(settings)
    if fetcher is None:
        fetcher = build_web_fetcher(
            allowlist=allowlist,
            max_bytes=max_bytes,
            web_settings=getattr(settings, "web", None),
        )
    result = await fetcher.extract_full_content(url, max_bytes=max_bytes)
    title = result.title
    if not title and result.content and result.content_type in MARKDOWN_TYPES:
        # A Markdown page has no <title>: its first H1 names it.
        title = markdown_title(result.content)
    page = FetchedPage(
        url=url,
        final_url=result.final_url or url,
        content_type=result.content_type,
        is_binary=result.is_binary,
        content=result.content,
        data=result.bytes,
        title=title,
        author=result.author,
        date=result.date,
        encoding_used=result.encoding_used,
        error=result.error,
    )
    logger.debug(
        "page_fetched",
        url=url,
        content_type=page.content_type,
        is_binary=page.is_binary,
        failed=page.error is not None,
    )
    return page


__all__ = [
    "MIN_PAGE_CONTENT_CHARS",
    "FetchedPage",
    "PageNames",
    "build_web_fetcher",
    "fetch_page",
    "page_names",
    "upload_fetch_limits",
]
