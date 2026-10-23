# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Parsers for the documents a site discovery reads: llms.txt, sitemaps, HTML.

Pure functions over bytes or text the hardened fetcher already downloaded
(size-capped). They resolve relative links against the document's own URL and
leave scope filtering, cleaning and deduplication to ``discovery``.
"""

from __future__ import annotations

import re
import unicodedata
import zlib
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import structlog
from bs4 import BeautifulSoup

from chaoscypher_core.services.sources.web.site_urls import page_identity


logger = structlog.get_logger(__name__)


# ``[text](url)`` or ``[text](url "title")`` (the llms.txt list-item shape).
_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_H1_RE = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.MULTILINE)
# Other llms.txt-convention files an llms.txt links to; they are not pages.
_LLMS_FILE_NAMES = frozenset({"llms.txt", "llms-full.txt", "llms-ctx.txt", "llms-ctx-full.txt"})
_GZIP_MAGIC = b"\x1f\x8b"


#: Media types whose body is Markdown (an llms.txt link's ``.md`` page).
MARKDOWN_TYPES = frozenset({"text/markdown", "text/x-markdown"})

_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def markdown_title(text: str) -> str:
    """The first level-one ATX heading of a Markdown document, outside code fences.

    Names a Markdown page (it has no ``<title>``) and an llms.txt file.

    Returns:
        The heading's text, or ``""`` when there is none.
    """
    fenced = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            heading = _H1_RE.match(line)
            if heading:
                return heading.group(1).strip()
    return ""


_TAG_RE = re.compile(r"<[^>]*>")
#: Markdown and markup punctuation a title never needs.
_MARKUP_CHARS = str.maketrans("", "", "`*#<>[]{}|~\\")
#: A Markdown link keeps its text: ``[delete_all](x)`` reads ``delete_all``.
_MD_LINK_TEXT_RE = re.compile(r"\[([^\]\n]*)\]\([^)\n]*\)")


def plain_title(text: str, max_chars: int) -> str:
    """A title as one plain line: tags, markup and control characters gone, capped.

    A site's title comes from its own llms.txt heading or page ``<title>``
    and is repeated to agents (the MCP server's answers), so it is cleaned to
    a short, inert line: HTML tags and Markdown punctuation removed, control
    and format characters dropped, whitespace collapsed, at most *max_chars*
    (cut at a word, with an ellipsis).

    Args:
        text: The title as found.
        max_chars: The longest title kept (``web.site_title_max_chars``).

    Returns:
        The cleaned title (``""`` when nothing printable is left).
    """
    stripped = _TAG_RE.sub(" ", _MD_LINK_TEXT_RE.sub(r"\1", text)).translate(_MARKUP_CHARS)
    printable = "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in stripped)
    line = " ".join(printable.split())
    if len(line) <= max_chars:
        return line
    cut = line[: max_chars - 1].rsplit(" ", 1)[0] or line[: max_chars - 1]
    return f"{cut}\u2026"


def parse_llms_txt(text: str, base_url: str) -> tuple[str, list[tuple[str, str]]]:
    """Read an llms.txt file: its H1 title and its links, ``.md`` spelling preferred.

    The llms.txt convention (llmstxt.org) is Markdown: an H1 with the site's
    name, a summary, then sections of ``- [name](url): notes`` lists. Every
    Markdown link in the file is taken (scope filtering drops the off-site
    ones); links to the convention's own files (``llms-full.txt`` ...) are
    skipped. When a page is listed both as ``/a`` and as ``/a.md`` (the
    Markdown twin many generators publish) only the ``.md`` link is kept, in
    the first one's position: the fetcher passes Markdown through unchanged.

    Args:
        text: The llms.txt body.
        base_url: The URL the file was served from (relative links resolve
            against it).

    Returns:
        ``(title, [(absolute_url, link_text), ...])``; the title is ``""``
        when the file has no H1.
    """
    title = markdown_title(text)
    links: list[tuple[str, str]] = []
    position: dict[str, int] = {}
    for match in _MD_LINK_RE.finditer(text):
        link_text, href = match.group(1).strip(), match.group(2)
        url = urljoin(base_url, href)
        if urlsplit(url).scheme not in {"http", "https"}:
            continue
        if urlsplit(url).path.rsplit("/", 1)[-1] in _LLMS_FILE_NAMES:
            continue
        page = page_identity(url)
        if page not in position:
            position[page] = len(links)
            links.append((url, link_text))
        elif urlsplit(url).path.endswith(".md") and not urlsplit(
            links[position[page]][0]
        ).path.endswith(".md"):
            links[position[page]] = (url, link_text)
    return title, links


def _inflate(body: bytes, max_bytes: int) -> bytes | None:
    """Gunzip *body*, refusing anything that inflates past *max_bytes*."""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        data = inflater.decompress(body, max_bytes)
    except zlib.error:
        return None
    if inflater.unconsumed_tail:
        logger.warning("site_sitemap_inflate_too_large", max_bytes=max_bytes)
        return None
    return data


def _local_name(tag: str) -> str:
    """``{namespace}loc`` -> ``loc``."""
    return tag.rsplit("}", 1)[-1]


def parse_sitemap(body: bytes, *, max_bytes: int) -> tuple[list[str], list[str]]:
    """Read a sitemap or a sitemap index (plain or gzipped XML).

    Args:
        body: The document as downloaded.
        max_bytes: Cap on the inflated size of a gzipped document.

    Returns:
        ``(page_urls, child_sitemap_urls)`` — a ``<urlset>`` fills the first,
        a ``<sitemapindex>`` the second. Anything else (HTML, junk, a gzip
        body past the cap) yields two empty lists.
    """
    if body.startswith(_GZIP_MAGIC):
        inflated = _inflate(body, max_bytes)
        if inflated is None:
            return [], []
        body = inflated
    try:
        # Python's bundled expat (2.4+) refuses entity-expansion bombs and
        # ElementTree never fetches external entities; the body is size-capped.
        root = ElementTree.fromstring(body)  # noqa: S314 - see the comment above
    except ElementTree.ParseError:
        return [], []
    kind = _local_name(root.tag)
    if kind not in {"urlset", "sitemapindex"}:
        return [], []
    locs = [
        (element.text or "").strip()
        for element in root.iter()
        if _local_name(element.tag) == "loc" and (element.text or "").strip()
    ]
    if kind == "urlset":
        return locs, []
    return [], locs


def parse_html_links(body: bytes, base_url: str) -> tuple[str, list[str]]:
    """Read an HTML page's ``<title>`` and the absolute URL of every ``<a href>``.

    Args:
        body: The page as downloaded (BeautifulSoup detects its encoding).
        base_url: The URL the page was served from (after redirects).

    Returns:
        ``(title, links)``: the whitespace-collapsed title (``""`` when
        absent) and the links in document order, resolved but not filtered.
    """
    soup = BeautifulSoup(body, "html.parser")
    title = " ".join(soup.title.get_text().split()) if soup.title else ""
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")
        if isinstance(href, str) and href.strip():
            links.append(urljoin(base_url, href.strip()))
    return title, links


__all__ = ["parse_html_links", "parse_llms_txt", "parse_sitemap", "plain_title"]
