# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Discover a docs site's pages from one address (site import design, decision 3).

``discover_site(url, settings)`` tries, in order, and stops at the first that
yields pages under the given address:

1. ``llms.txt`` at the given path, then at the site root;
2. sitemaps: ``Sitemap:`` lines in robots.txt, ``sitemap.xml`` at the given
   path and at the root, and the sitemap indexes they lead to;
3. a breadth-first crawl of HTML links from the given page, to
   ``web.site_crawl_max_depth`` links deep.

A :class:`DiscoveryBudget` (the preview's, from the ``web.site_preview_*``
settings) bounds the whole discovery: one deadline across every fetch, a few
sitemaps, a smaller document cap, a shorter crawl, and no Crawl-delay pacing;
past it the discovery answers with what it found, marked ``capped``.

Every candidate is cleaned (fragment dropped), deduplicated with the trailing
slash folded, kept inside the given scheme, host and path prefix, filtered by
robots.txt (``web.respect_robots_txt``; our ``web.user_agent``; a Crawl-delay
honoured up to ``web.crawl_delay_max_seconds``), and capped at
``web.site_max_pages``. An ``http://`` address of a site that answers on
``https://`` is re-based on its robots.txt redirect before anything else is
read. Documents are parsed in worker threads, off the event loop. Every
address passes the strict SSRF check before it is fetched, and every fetch
goes through the hardened ``WebFetcherPort`` (pinned IP, redirect re-checks,
byte cap, User-Agent) under one :class:`CrawlThrottle`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from enum import StrEnum
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import structlog
from pydantic import BaseModel, ConfigDict

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.web.fetch import build_web_fetcher
from chaoscypher_core.services.sources.web.site_parsers import (
    parse_html_links,
    parse_llms_txt,
    parse_sitemap,
    plain_title,
)
from chaoscypher_core.services.sources.web.site_urls import SiteScope, clean_url, url_key
from chaoscypher_core.services.sources.web.throttle import CrawlThrottle
from chaoscypher_core.settings import WebSettings
from chaoscypher_core.utils.url_safety import validate_url_safety


if TYPE_CHECKING:
    from chaoscypher_core.ports.web import RawResponse, WebFetcherPort


logger = structlog.get_logger(__name__)


class DiscoveryMethod(StrEnum):
    """How a site's pages were found."""

    LLMS_TXT = "llms_txt"
    SITEMAP = "sitemap"
    CRAWL = "crawl"
    NONE = "none"  # nothing discoverable under the address

    @property
    def label(self) -> str:
        """How the method is named to a user: "found by llms.txt / sitemap / crawling"."""
        return _METHOD_LABELS[self]


#: How each method is named to a user ("71 pages found by llms.txt"); the web
#: UI's site offer names them the same way.
_METHOD_LABELS = {
    DiscoveryMethod.LLMS_TXT: "llms.txt",
    DiscoveryMethod.SITEMAP: "sitemap",
    DiscoveryMethod.CRAWL: "crawling",
    DiscoveryMethod.NONE: "nothing",
}


class SitePage(BaseModel):
    """One discovered page: its address and, when the method knows it, its title."""

    model_config = ConfigDict(frozen=True)

    url: str
    title: str = ""


class SiteManifest(BaseModel):
    """What a discovery found at an address.

    Attributes:
        url: The address discovered (cleaned).
        title: The site's name: llms.txt's H1, else the given page's
            ``<title>``, else the host; one line, markup and control
            characters removed, at most ``web.site_title_max_chars``.
        method: Which method found the pages (``none`` when nothing was found).
        pages: The pages, in the order the method listed them, at most
            ``web.site_max_pages``. Titles are the llms.txt link text or the
            crawled page's ``<title>``; a sitemap gives none (``""``).
        crawl_delay_seconds: The gap the site's requests must keep (our
            setting, or robots.txt's Crawl-delay when larger); the import
            paces itself by it.
        capped: True when the method found more pages than
            ``web.site_max_pages``, or a bound stopped the discovery with
            documents still unread (the crawl's fetch budget, the sitemap
            count, a budget's deadline): not every page is listed.
    """

    model_config = ConfigDict(frozen=True)

    url: str
    title: str
    method: DiscoveryMethod
    pages: list[SitePage]
    crawl_delay_seconds: float
    capped: bool = False


class DiscoveryBudget(BaseModel):
    """A bound on one whole discovery: its time, its sitemaps, its documents, its crawl.

    The site preview runs on one (:meth:`for_preview`) so the Add Source
    dialog's check stays cheap; the import's own discovery runs without and
    finds the rest. When the budget runs out the discovery answers with what
    it found, marked ``capped``.

    Attributes:
        max_pages: Page fetches the crawl fallback may make, the start page's
            included (never more than ``web.site_max_pages``).
        max_seconds: Seconds from the discovery's start after which no fetch
            starts (robots.txt, llms.txt, sitemaps and the crawl alike).
        max_sitemaps: Sitemap documents fetched at most (never more than
            ``web.site_max_sitemaps``).
        max_document_bytes: Largest document downloaded or inflated (never more
            than ``web.site_document_max_bytes``).
        follow_crawl_delay: Whether robots.txt's Crawl-delay paces the fetches
            (the preview's few fetches ignore it).
    """

    model_config = ConfigDict(frozen=True)

    max_pages: int
    max_seconds: float
    max_sitemaps: int
    max_document_bytes: int
    follow_crawl_delay: bool = False

    @classmethod
    def for_preview(cls, web: WebSettings) -> DiscoveryBudget:
        """The site preview's budget, from the ``web.site_preview_*`` settings."""
        return cls(
            max_pages=web.site_preview_max_pages,
            max_seconds=web.site_preview_seconds,
            max_sitemaps=web.site_preview_max_sitemaps,
            max_document_bytes=web.site_preview_document_max_bytes,
        )


# Link targets the crawl never fetches: not HTML pages (PDFs linked from a site
# are a non-goal; single-URL import handles a PDF address).
_NON_PAGE_SUFFIXES = (
    ".pdf", ".zip", ".gz", ".tgz", ".tar", ".bz2", ".xz", ".7z", ".exe", ".dmg", ".iso",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".avif",
    ".css", ".js", ".mjs", ".map", ".json", ".xml", ".txt", ".csv",
    ".mp3", ".mp4", ".webm", ".mov", ".avi", ".wav", ".ogg",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
)  # fmt: skip
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
# An llms.txt is Markdown; an HTML answer is a single-page app's shell, not the file.
_LLMS_TYPES = frozenset({"", "text/plain", "text/markdown", "text/x-markdown"})


def _safe_clean(url: str) -> str | None:
    """:func:`clean_url`, or ``None`` for a URL that cannot be parsed (a bad port)."""
    try:
        return clean_url(url)
    except ValueError:
        return None


class _Robots:
    """robots.txt for one origin, read with the stdlib parser's status rules."""

    def __init__(self, user_agent: str, *, respect: bool) -> None:
        """Rules for ``user_agent``; ``respect`` False allows everything."""
        self._user_agent = user_agent
        self._respect = respect
        self._parser = RobotFileParser()
        # True / False: allow / disallow everything (a status-code verdict);
        # None: ask the parsed rules. Everything is allowed until a load.
        self._verdict: bool | None = True
        self.sitemaps: list[str] = []
        self.crawl_delay: float | None = None

    def load(self, response: RawResponse | None) -> None:
        """Apply a robots.txt response with ``RobotFileParser.read``'s status rules.

        401/403 and 5xx disallow everything (RFC 9309 § 2.3.1.4 for 5xx); any
        other 4xx allows everything; an unreachable robots.txt counts as a
        5xx. ``Sitemap:`` lines are read whatever ``respect`` says: they are
        hints, not restrictions.
        """
        self._parser = RobotFileParser()
        self.sitemaps, self.crawl_delay = [], None
        if response is None or response.status_code >= 500 or response.status_code in (401, 403):
            self._verdict = False
            return
        if response.status_code >= 400:
            self._verdict = True
            return
        self._verdict = None
        self._parser.parse(response.body.decode("utf-8", "replace").splitlines())
        self._parser.modified()
        self.sitemaps = list(self._parser.site_maps() or [])
        delay = self._parser.crawl_delay(self._user_agent)
        self.crawl_delay = float(delay) if delay is not None else None

    def allows(self, url: str) -> bool:
        """Whether robots.txt lets our User-Agent fetch *url* (always, when not respected)."""
        if not self._respect:
            return True
        if self._verdict is not None:
            return self._verdict
        return self._parser.can_fetch(self._user_agent, url)


class SiteGate:
    """Where a site's pages may be: its scope (after the start's redirects) and its robots.txt.

    A discovery hands its gate to the import, which refuses a page whose
    final address, after the fetch's redirects, the gate does not admit: a
    page that redirected off the site or onto a path robots.txt disallows.
    """

    def __init__(self, scope: SiteScope, robots: _Robots) -> None:
        """Hold the discovery's final scope and its robots.txt rules."""
        self._scope = scope
        self._robots = robots

    def admits(self, url: str) -> bool:
        """Whether *url* is inside the site's scope and robots.txt lets us fetch it."""
        cleaned = _safe_clean(url)
        return (
            cleaned is not None and self._scope.contains(cleaned) and self._robots.allows(cleaned)
        )


class _Discovery:
    """One discovery run: scope, robots.txt, throttle and fetcher for one address."""

    def __init__(
        self,
        url: str,
        web: WebSettings,
        *,
        fetcher: WebFetcherPort,
        throttle: CrawlThrottle,
        budget: DiscoveryBudget | None = None,
    ) -> None:
        """Hold one discovery's address, settings, fetcher, throttle and budget."""
        self.given = url
        self.scope = SiteScope.from_url(url)
        self.web = web
        self.fetcher = fetcher
        self.throttle = throttle
        self.robots = _Robots(web.user_agent, respect=web.respect_robots_txt)
        self.seen: set[str] = set()
        self.max_bytes = web.site_document_max_bytes
        self.max_sitemaps = web.site_max_sitemaps
        self.follow_crawl_delay = True
        # The whole discovery's deadline on the throttle's clock (a budget's),
        # whether it turned a fetch away, and whether a bound left documents unread.
        self.deadline: float | None = None
        self.out_of_time = False
        self.left_unread = False
        if budget is not None:
            self.max_bytes = min(self.max_bytes, budget.max_document_bytes)
            self.max_sitemaps = min(self.max_sitemaps, budget.max_sitemaps)
            self.follow_crawl_delay = budget.follow_crawl_delay
            self.deadline = throttle.now() + budget.max_seconds

    # ------------------------------------------------------------------ fetching

    async def fetch(self, url: str, *, check_robots: bool = True) -> RawResponse | None:
        """Fetch *url* if it is safe and allowed, through the throttle and the hardened fetcher."""
        if not validate_url_safety(url, strict=True):
            logger.warning("site_discovery_url_blocked", url=url)
            return None
        if check_robots and not self.robots.allows(url):
            logger.debug("site_discovery_robots_disallowed", url=url)
            return None
        async with self.throttle.slot(deadline=self.deadline) as allowed:
            if not allowed:
                self.out_of_time = True
                return None
            if self.deadline is None:
                return await self.fetcher.fetch_raw(url, max_bytes=self.max_bytes)
            try:
                # A fetch that started in time still ends by the deadline.
                async with asyncio.timeout(max(0.0, self.deadline - self.throttle.now())):
                    return await self.fetcher.fetch_raw(url, max_bytes=self.max_bytes)
            except TimeoutError:
                self.out_of_time = True
                return None

    def _ok(self, response: RawResponse | None, types: frozenset[str]) -> bool:
        """A 200 response of one of *types*, still on the site after its redirects."""
        return (
            response is not None
            and response.status_code == 200
            and response.content_type in types
            and self.scope.same_origin(response.final_url)
        )

    async def load_robots(self) -> None:
        """Read the scope origin's robots.txt; raise the throttle to a larger Crawl-delay.

        A robots.txt that redirects to the same host's ``/robots.txt`` under
        the other scheme (an ``http://`` address of an ``https://`` site) is
        the site's scheme redirect: the scope moves to that scheme and the
        response is its robots.txt. Any other redirect off the origin counts
        as unreachable. A Crawl-delay is honoured up to
        ``web.crawl_delay_max_seconds`` (not at all under a preview's budget).
        """
        response = await self.fetch(f"{self.scope.origin}/robots.txt", check_robots=False)
        if response is not None and not self.scope.same_origin(response.final_url):
            moved = _safe_clean(response.final_url)
            parts = urlsplit(moved) if moved is not None else None
            if (
                parts is not None
                and parts.netloc == self.scope.netloc
                and parts.path == "/robots.txt"
                and parts.scheme != self.scope.scheme
            ):
                self.scope = dataclasses.replace(self.scope, scheme=parts.scheme)
            else:
                response = None  # redirected off the site: treat as unreachable
        self.robots.load(response)
        delay = self.robots.crawl_delay
        if self.web.respect_robots_txt and delay is not None and self.follow_crawl_delay:
            self.throttle.raise_delay(min(delay, self.web.crawl_delay_max_seconds))

    async def resolve_start(self) -> RawResponse | None:
        """Read robots.txt, then fetch the given page, following it within the same host.

        A redirect to the same host re-bases the scope (``http`` to
        ``https``, ``/docs`` to ``/documentation/``); one to another host
        does not, and the crawl then has no start page.

        Returns:
            The start page's response when it is a successful HTML page
            inside the scope, else ``None``.
        """
        await self.load_robots()
        start = await self.fetch(self.given)
        final = _safe_clean(start.final_url) if start is not None else None
        if start is None or final is None or start.status_code >= 400:
            return None
        if not self.scope.contains(final):
            if urlsplit(final).hostname != urlsplit(self.scope.origin).hostname:
                return None
            origin_before = self.scope.origin
            self.scope = SiteScope.from_url(final)
            if self.scope.origin != origin_before:
                await self.load_robots()
        return start if start.content_type in _HTML_TYPES else None

    # ------------------------------------------------------------------ filtering

    def admit(self, raw_url: str) -> str | None:
        """Clean *raw_url* and claim it: in scope, robots-allowed and not seen before."""
        url = _safe_clean(raw_url)
        if url is None or not self.scope.contains(url) or not self.robots.allows(url):
            return None
        key = url_key(url)
        if key in self.seen:
            return None
        self.seen.add(key)
        return url

    def admit_pages(self, candidates: list[tuple[str, str]]) -> list[SitePage]:
        """The admitted ``(url, title)`` candidates as pages, in order."""
        pages: list[SitePage] = []
        for raw_url, title in candidates:
            url = self.admit(raw_url)
            if url is not None:
                pages.append(SitePage(url=url, title=title))
        return pages

    # ------------------------------------------------------------------ methods

    async def from_llms_txt(self) -> tuple[str, list[SitePage]]:
        """Method 1: llms.txt at the given path, then at the site root."""
        for candidate in dict.fromkeys(
            [self.scope.document_url("llms.txt"), f"{self.scope.origin}/llms.txt"]
        ):
            response = await self.fetch(candidate)
            if response is None or not self._ok(response, _LLMS_TYPES):
                continue
            title, links = await asyncio.to_thread(
                parse_llms_txt, response.body.decode("utf-8", "replace"), response.final_url
            )
            self.seen.clear()
            pages = self.admit_pages(links)
            if pages:
                return title, pages
        self.seen.clear()
        return "", []

    async def from_sitemaps(self) -> list[SitePage]:
        """Method 2: robots.txt ``Sitemap:`` lines and ``sitemap.xml``, through their indexes."""
        queue = [
            *self.robots.sitemaps,
            self.scope.document_url("sitemap.xml"),
            f"{self.scope.origin}/sitemap.xml",
        ]
        fetched: set[str] = set()
        pages: list[SitePage] = []
        while queue and len(pages) <= self.web.site_max_pages:
            if len(fetched) >= self.max_sitemaps:
                self.left_unread = True  # documents still waiting: not every page is listed
                break
            candidate = _safe_clean(queue.pop(0))
            if candidate is None or candidate in fetched or not self.scope.same_origin(candidate):
                continue  # a sitemap on another host is never followed
            fetched.add(candidate)
            response = await self.fetch(candidate)
            if response is None or response.status_code != 200:
                continue
            if not self.scope.same_origin(response.final_url):
                continue
            page_urls, children = await asyncio.to_thread(
                parse_sitemap, response.body, max_bytes=self.max_bytes
            )
            pages.extend(self.admit_pages([(url, "") for url in page_urls]))
            queue.extend(children)
        self.seen.clear()
        return pages

    async def _crawled_page(
        self, response: RawResponse | None, listed: set[str]
    ) -> tuple[SitePage, list[str]] | None:
        """A fetched crawl candidate as a page plus its links, or ``None`` if it is not one."""
        if not self._ok(response, _HTML_TYPES) or response is None:
            return None
        url = _safe_clean(response.final_url)
        if (
            url is None
            or not self.scope.contains(url)
            or not self.robots.allows(url)
            or url_key(url) in listed
        ):
            return None  # redirected off the path, onto a disallowed or an already-listed page
        listed.add(url_key(url))
        self.seen.add(url_key(url))
        title, links = await asyncio.to_thread(parse_html_links, response.body, response.final_url)
        return SitePage(url=url, title=title), links

    async def from_crawl(
        self, start: RawResponse, bounds: DiscoveryBudget | None = None
    ) -> tuple[list[SitePage], bool]:
        """Method 3: breadth-first HTML link crawl inside the scope.

        Every fetch, the start page's included, counts against
        ``web.site_max_pages`` (and ``bounds.max_pages``), so the
        crawl's requests are bounded too; a level's links are fetched
        ``web.crawl_concurrency`` at a time. The discovery's deadline (a
        budget's) holds here as everywhere.

        Returns:
            ``(pages, capped)``; ``capped`` is True when the fetch budget or
            the time ran out with links still waiting.
        """
        self.seen = {url_key(self.given)}
        max_fetches = self.web.site_max_pages
        if bounds is not None:
            max_fetches = min(max_fetches, bounds.max_pages)
        budget = max_fetches - 1
        listed: set[str] = set()
        pages: list[SitePage] = []
        capped = False
        level: list[RawResponse | None] = [start]
        for depth in range(self.web.site_crawl_max_depth + 1):
            frontier: list[str] = []
            for response in level:
                crawled = await self._crawled_page(response, listed)
                if crawled is None:
                    continue
                page, links = crawled
                pages.append(page)
                if depth < self.web.site_crawl_max_depth:
                    frontier.extend(url for url in map(self._next_link, links) if url)
            if not frontier:
                break
            if budget <= 0:
                capped = True
                break
            batch = frontier[:budget]
            capped = capped or len(frontier) > len(batch)
            budget -= len(batch)
            level = list(await asyncio.gather(*(self.fetch(url) for url in batch)))
        if self.out_of_time:
            logger.info("site_discovery_crawl_out_of_time", url=self.given, pages=len(pages))
        return pages, capped or self.out_of_time

    def _next_link(self, raw_url: str) -> str | None:
        """A link worth fetching next: admitted and not an obvious non-page file."""
        url = _safe_clean(raw_url)
        if url is None or urlsplit(url).path.lower().endswith(_NON_PAGE_SUFFIXES):
            return None
        return self.admit(url)


def pages_found(manifest: SiteManifest) -> str:
    """What a discovery found, in words: "71 pages found by llms.txt".

    A capped discovery says "at least" (the count is where it stopped); one
    that found nothing says "no pages found". The CLI and the MCP server
    answer with it.
    """
    count = len(manifest.pages)
    if count == 0 or manifest.method is DiscoveryMethod.NONE:
        return "no pages found"
    noun = "page" if count == 1 else "pages"
    amount = f"at least {count}" if manifest.capped else f"{count}"
    return f"{amount} {noun} found by {manifest.method.label}"


def _validated_start(url: str) -> str:
    """Clean the given address, refusing a non-http(s) or unsafe one.

    Raises:
        ValidationError: Not an absolute http(s) URL, or it fails the strict
            SSRF policy (resolves to a private, loopback or metadata address,
            or does not resolve).
    """
    cleaned = _safe_clean(url) if urlsplit(url.strip()).scheme in {"http", "https"} else None
    if cleaned is None or not urlsplit(cleaned).hostname:
        msg = "A site address must be an absolute http(s) URL."
        raise ValidationError(msg, field="url")
    if not validate_url_safety(cleaned, strict=True):
        msg = "The site address is blocked by the URL safety policy."
        raise ValidationError(msg, field="url")
    return cleaned


async def discover_site(
    url: str,
    settings: object,
    *,
    fetcher: WebFetcherPort | None = None,
    throttle: CrawlThrottle | None = None,
    budget: DiscoveryBudget | None = None,
) -> SiteManifest:
    """Find the pages of the docs site at *url*: :func:`discover_site_gate`'s manifest.

    See :func:`discover_site_gate` for the arguments, the result and the errors.
    """
    manifest, _gate = await discover_site_gate(
        url, settings, fetcher=fetcher, throttle=throttle, budget=budget
    )
    return manifest


async def discover_site_gate(
    url: str,
    settings: object,
    *,
    fetcher: WebFetcherPort | None = None,
    throttle: CrawlThrottle | None = None,
    budget: DiscoveryBudget | None = None,
) -> tuple[SiteManifest, SiteGate]:
    """Find the pages of the docs site at *url* (llms.txt, then sitemaps, then a crawl).

    Args:
        url: The site address the user gave (``https://docs.example.com`` or
            ``https://example.com/docs/``).
        settings: App ``Settings`` or ``EngineSettings`` (anything with a
            ``web`` group): politeness, caps and the User-Agent.
        fetcher: The hardened fetcher; ``None`` builds a ``WebScraper``.
        throttle: The site's request gate; ``None`` builds one from
            ``web.crawl_delay_seconds`` and ``web.crawl_concurrency``. Pass
            the import's own throttle to pace discovery and import together.
        budget: A bound on the whole discovery (the preview passes
            :meth:`DiscoveryBudget.for_preview`); ``None`` lets it run to the
            ``web.site_*`` caps.

    Returns:
        ``(manifest, gate)``: the :class:`SiteManifest` (``method`` is
        ``none`` with no pages when nothing is discoverable under the
        address) and the :class:`SiteGate` a page's final address must pass.

    Raises:
        ValidationError: The address is not an http(s) URL or fails the
            strict SSRF check.
    """
    start_url = _validated_start(url)
    web = getattr(settings, "web", None)
    web = web if isinstance(web, WebSettings) else WebSettings()
    if fetcher is None:
        fetcher = build_web_fetcher(
            allowlist=[], max_bytes=web.site_document_max_bytes, web_settings=web
        )
    if throttle is None:
        throttle = CrawlThrottle(
            delay_seconds=web.crawl_delay_seconds, concurrency=web.crawl_concurrency
        )
    started = time.monotonic()
    run = _Discovery(start_url, web, fetcher=fetcher, throttle=throttle, budget=budget)
    logger.info("site_discovery_started", url=start_url)

    start = await run.resolve_start()
    start_title = ""
    if start is not None:
        start_title, _links = await asyncio.to_thread(parse_html_links, start.body, start.final_url)

    method, title, capped = DiscoveryMethod.NONE, start_title, False
    pages: list[SitePage] = []
    llms_title, llms_pages = await run.from_llms_txt()
    if llms_pages:
        method, title, pages = DiscoveryMethod.LLMS_TXT, llms_title or start_title, llms_pages
    elif sitemap_pages := await run.from_sitemaps():
        method, pages = DiscoveryMethod.SITEMAP, sitemap_pages
    elif start is not None:
        pages, capped = await run.from_crawl(start, budget)
        method = DiscoveryMethod.CRAWL if pages else DiscoveryMethod.NONE

    manifest = SiteManifest(
        url=start_url,
        # The title reaches agents (MCP answers): one clean, capped line.
        title=plain_title(title, web.site_title_max_chars) or run.scope.netloc,
        method=method,
        pages=pages[: web.site_max_pages],
        crawl_delay_seconds=throttle.delay_seconds,
        capped=(capped or len(pages) > web.site_max_pages or run.out_of_time or run.left_unread),
    )
    logger.info(
        "site_discovery_finished",
        url=start_url,
        method=manifest.method.value,
        page_count=len(manifest.pages),
        capped=manifest.capped,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return manifest, SiteGate(run.scope, run.robots)


__all__ = [
    "DiscoveryBudget",
    "DiscoveryMethod",
    "SiteGate",
    "SiteManifest",
    "SitePage",
    "discover_site",
    "discover_site_gate",
    "pages_found",
]
