# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Web fetch port: what Core services need from the hardened web fetcher.

``chaoscypher_core.adapters.web.WebScraper`` is the implementation: every
request dials an IP vetted by the strict SSRF policy, re-checks each redirect
hop, caps the body size and sends ``WebSettings.user_agent``. Services such as
``fetch_page`` and ``discover_site`` (``services/sources/web/``) depend on this
protocol, not on the adapter, so tests can hand them a fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class FetchResult:
    """Result of fetching a URL and extracting its content.

    Attributes:
        content: Decoded text content (trafilatura Markdown for HTML, the body
            verbatim for other textual types). Empty string for binary fetches.
        content_type: Bare media type (e.g. ``application/pdf``), with
            any ``charset=`` / ``boundary=`` parameters stripped.
        is_binary: True when the response was treated as binary
            (Content-Type outside the textual set). The ``bytes``
            field carries the raw payload in that case.
        bytes: Raw response body for binary fetches. ``None`` for
            textual fetches.
        url: The originally requested URL.
        final_url: The logical URL after redirects (never the pinned IP).
            Empty when the fetch failed before a response arrived.
        title: Page title extracted by trafilatura. Empty for binary.
        author: Author metadata extracted by trafilatura. ``None``
            when not available or when the response is binary.
        date: Publication date extracted by trafilatura. ``None`` when
            not available or when the response is binary.
        encoding_used: The encoding label used to decode the body
            (e.g. ``"utf-8"``, ``"cp1252"``). ``None`` for binary
            fetches and for the short-circuit error paths.
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
    final_url: str = ""
    title: str = ""
    author: str | None = None
    date: str | None = None
    encoding_used: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class RawResponse:
    """A fetched response with no extraction applied (robots.txt, sitemaps, HTML).

    Attributes:
        url: The originally requested URL.
        final_url: The logical URL after redirects (never the pinned IP).
        status_code: HTTP status of the final, non-redirect response.
        content_type: Bare, lowercased media type (parameters stripped).
        body: The response body; empty for a status of 400 or above.
    """

    url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes


class WebFetcherPort(Protocol):
    """The hardened web fetcher Core services fetch through."""

    async def extract_full_content(self, url: str, *, max_bytes: int | None = None) -> FetchResult:
        """Fetch *url* and extract its content (Markdown for HTML, verbatim text otherwise).

        Raises:
            ValidationError: The response's Content-Type is outside the
                fetcher's allowlist, or a binary body exceeds its size cap.
        """
        ...

    async def fetch_raw(self, url: str, *, max_bytes: int | None = None) -> RawResponse | None:
        """Fetch *url* and return the final response unprocessed.

        Returns ``None`` when the address (or a redirect hop) is blocked by the
        strict SSRF policy, the redirect budget runs out, the body exceeds
        *max_bytes*, or the request fails at the network level.
        """
        ...


__all__ = ["FetchResult", "RawResponse", "WebFetcherPort"]
