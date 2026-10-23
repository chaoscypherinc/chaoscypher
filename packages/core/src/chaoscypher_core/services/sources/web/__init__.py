# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Web sources: fetching a page and discovering a site's pages.

- ``fetch_page``: the one page fetch every URL import uses (worker handler,
  CLI, MCP server, site import).
- ``discover_site``: find a docs site's pages (llms.txt, sitemap, link crawl).
- ``CrawlThrottle``: the per-site request gate both share.
- ``SiteProgress`` / ``progress_by_site``: a site's progress, computed from
  its pages counted by status.

Both depend on ``chaoscypher_core.ports.web.WebFetcherPort``; the production
fetcher is ``chaoscypher_core.adapters.web.WebScraper``.
"""

from chaoscypher_core.services.sources.web.discovery import (
    DiscoveryBudget,
    DiscoveryMethod,
    SiteGate,
    SiteManifest,
    SitePage,
    discover_site,
    discover_site_gate,
    pages_found,
)
from chaoscypher_core.services.sources.web.fetch import (
    MIN_PAGE_CONTENT_CHARS,
    FetchedPage,
    PageNames,
    build_web_fetcher,
    fetch_page,
    page_names,
    upload_fetch_limits,
)
from chaoscypher_core.services.sources.web.site_parsers import plain_title
from chaoscypher_core.services.sources.web.site_progress import SiteProgress, progress_by_site
from chaoscypher_core.services.sources.web.throttle import CrawlThrottle


__all__ = [
    "MIN_PAGE_CONTENT_CHARS",
    "CrawlThrottle",
    "DiscoveryBudget",
    "DiscoveryMethod",
    "FetchedPage",
    "PageNames",
    "SiteGate",
    "SiteManifest",
    "SitePage",
    "SiteProgress",
    "build_web_fetcher",
    "discover_site",
    "discover_site_gate",
    "fetch_page",
    "page_names",
    "pages_found",
    "plain_title",
    "progress_by_site",
    "upload_fetch_limits",
]
