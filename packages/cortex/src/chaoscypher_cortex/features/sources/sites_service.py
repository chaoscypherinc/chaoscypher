# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Imported docs sites: preview, import, read, list, refresh, delete (spec 2026-10-22).

Discovery and the import run in Core: ``discover_site`` for a preview, the
``import_site`` queue operation for the import. A site's progress is
computed from its pages counted by status (``progress_by_site``), never
stored. Raises Core exceptions; the shared error layer maps them to HTTP.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import ConflictError, NotFoundError, ValidationError
from chaoscypher_core.models import SiteStatus
from chaoscypher_core.operations import queue_utils
from chaoscypher_core.operations.sources.site_runs import (
    exclusion_keys,
    refuse_while_running,
    site_leaves_out,
    site_run_live,
)
from chaoscypher_core.services.sources.web import (
    DiscoveryBudget,
    SiteProgress,
    discover_site,
    progress_by_site,
)
from chaoscypher_core.utils.url_safety import validate_url_safety
from chaoscypher_cortex.features.sources.models import (
    PaginatedSitesResponse,
    PaginatedSourcesResponse,
    SiteImportRequest,
    SitePageInfo,
    SitePreviewResponse,
    SiteResponse,
    SourceSummaryResponse,
)
from chaoscypher_cortex.shared.api.models import PaginationMetadata


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.ports.storage_sites import SourceSiteRecord
    from chaoscypher_cortex.features.sources.sites_repository import SitesRepository


logger = structlog.get_logger(__name__)


def _require_safe(url: str) -> None:
    """The strict SSRF check every site address passes before anything is fetched.

    Raises:
        ValidationError: The address is loopback, private, link-local,
            reserved, a cloud metadata endpoint, or does not resolve.
    """
    if not validate_url_safety(url, strict=True):
        msg = "The site address is blocked by the URL safety policy."
        raise ValidationError(msg, field="url")


class SitesService:
    """Business logic for the ``/sources/sites`` routes."""

    def __init__(
        self,
        repository: SitesRepository,
        *,
        settings: Settings,
        delete_source: Callable[..., bool],
        list_sources: Callable[..., dict[str, Any]],
    ) -> None:
        """Initialize the service.

        Args:
            repository: The site repository, scoped to the active database.
            settings: App settings (web politeness and caps, priorities).
            delete_source: The existing source delete (``SourceService.
                delete_source``: graph release, search and file cleanup), used
                to remove a site's pages on request (``leave_out_of_site``
                False: the site row goes next, so its list is not written).
            list_sources: The existing source list (``SourceService.
                list_sources_enriched``: tags, durations, domain icons), used
                to list a site's pages with its ``site_id`` filter.
        """
        self._repo = repository
        self._settings = settings
        self._delete_source = delete_source
        self._list_sources = list_sources

    # ---- Responses ----

    def _response(self, site: SourceSiteRecord, progress: SiteProgress | None) -> SiteResponse:
        """The site row as a ``SiteResponse`` (no progress: an empty one)."""
        return SiteResponse(
            id=site["id"],
            url=site["url"],
            title=site["title"],
            discovery_method=site["discovery_method"],  # type: ignore[arg-type]  # a DiscoveryMethod value; pydantic validates it
            status=SiteStatus(site["status"]),
            run_in_progress=site_run_live(site),
            page_count=site["page_count"],
            options=dict(site["options"] or {}),
            error_message=site["error_message"],
            created_at=site["created_at"],
            last_checked_at=site["last_checked_at"],
            progress=progress or SiteProgress.empty(),
            excluded_pages=list(site["excluded_pages"]),
        )

    def _require_site(self, site_id: str) -> SourceSiteRecord:
        """The site, or ``NotFoundError``."""
        site = self._repo.get(site_id)
        if site is None:
            raise NotFoundError("Site", site_id)
        return site

    # ---- Routes ----

    async def preview(self, url: str) -> SitePreviewResponse:
        """Discover the site at ``url`` without importing anything.

        The whole discovery runs on the preview's budget (the
        ``web.site_preview_*`` settings: one deadline, a few sitemaps, small
        documents, a short crawl, no Crawl-delay pacing) so the answer comes in
        seconds; past it, the answer is what was found, ``capped``. The
        import's own discovery has no such budget.

        Raises:
            ValidationError: The address fails the strict SSRF check.
        """
        _require_safe(url)
        manifest = await discover_site(
            url, self._settings, budget=DiscoveryBudget.for_preview(self._settings.web)
        )
        return SitePreviewResponse(
            url=manifest.url,
            title=manifest.title,
            method=manifest.method,
            page_count=len(manifest.pages),
            pages=[SitePageInfo(url=p.url, title=p.title) for p in manifest.pages],
            capped=manifest.capped,
        )

    async def create(self, request: SiteImportRequest) -> SiteResponse:
        """Create the site row and enqueue its import.

        Raises:
            ValidationError: The address fails the strict SSRF check, or more
                pages were excluded than ``web.site_max_pages``.
            ConflictError: The site (the same address scope) is already
                imported in this database; ``details["site"]`` is that site,
                which "Check for updates" refreshes.
        """
        _require_safe(request.url)
        # Refused before anything is looked up.
        exclusion_keys(request.exclude, site_url=request.url)
        existing = self._repo.find_by_scope(
            request.url, page_size=self._settings.pagination.default_page_size
        )
        if existing is not None:
            current = self.get(existing["id"])
            msg = f"{existing['title']} is already imported; check it for updates instead."
            raise ConflictError(msg, details={"site": current.model_dump(mode="json")})
        site = self._repo.create(
            url=request.url, options=request.options.upload_options(), exclude=request.exclude
        )
        await self._enqueue(site, refresh=False)
        logger.info("site_import_queued", site_id=site["id"], url=request.url)
        return self._response(site, None)

    def get(self, site_id: str) -> SiteResponse:
        """The site with its progress.

        Raises:
            NotFoundError: No such site in the active database.
        """
        site = self._require_site(site_id)
        progress = progress_by_site(self._repo.count_pages([site_id])).get(site_id)
        return self._response(site, progress)

    def list_sites(self, *, page: int, page_size: int) -> PaginatedSitesResponse:
        """One page of sites, newest first, each with its progress."""
        sites, total = self._repo.list_sites(page=page, page_size=page_size)
        progress = progress_by_site(self._repo.count_pages([s["id"] for s in sites]))
        return PaginatedSitesResponse(
            data=[self._response(s, progress.get(s["id"])) for s in sites],
            pagination=PaginationMetadata.for_page(total=total, page=page, page_size=page_size),
        )

    def list_pages(self, site_id: str, *, page: int, page_size: int) -> PaginatedSourcesResponse:
        """One page of the site's pages, as the sources list shows them (gone ones included).

        ``site_left_out`` marks a page the site leaves out (it keeps what it
        has and is not checked for updates).

        Raises:
            NotFoundError: No such site in the active database.
        """
        site = self._require_site(site_id)
        result = self._list_sources(page=page, page_size=page_size, site_id=site_id)
        return PaginatedSourcesResponse(
            data=[
                SourceSummaryResponse(**s, site_left_out=site_leaves_out(site, s.get("origin_url")))
                for s in result["sources"]
            ],
            pagination=PaginationMetadata.for_page(
                total=result["total"], page=page, page_size=page_size
            ),
        )

    async def refresh(self, site_id: str, *, exclude: list[str] | None = None) -> SiteResponse:
        """Enqueue a refresh: discovery again, every page fetched, only what changed re-imported.

        A run whose worker was killed (stale past the operations timeout) does
        not block it; the refresh resumes from that run's cursor. The pages
        the site leaves out stay out; ``exclude`` replaces them (``[]``
        brings every page back).

        Raises:
            NotFoundError: No such site in the active database.
            ConflictError: A live run of this site is queued or under way.
            ValidationError: More pages to leave out than ``web.site_max_pages``.
        """
        site = self._require_site(site_id)
        refuse_while_running(site, "a check for updates")
        self._repo.begin_refresh(site_id, exclude=exclude)
        await self._enqueue(site, refresh=True)
        logger.info("site_refresh_queued", site_id=site_id)
        return self.get(site_id)

    async def delete(self, site_id: str, *, delete_pages: bool) -> None:
        """Delete the site; with ``delete_pages``, its pages first, through the source delete.

        Raises:
            NotFoundError: No such site in the active database.
            ConflictError: A live run of the site is queued or under way (it
                would die on the missing row); a stale run does not count.
        """
        refuse_while_running(self._require_site(site_id), "deleting it")
        if delete_pages:
            for page in self._repo.list_pages(site_id):
                # The source delete releases the page's graph rows and cleans
                # its search rows and files; it is sync and may rmtree.
                await asyncio.to_thread(self._delete_source, page["id"], leave_out_of_site=False)
        self._repo.delete(site_id)
        logger.info("site_deleted", site_id=site_id, pages_deleted=delete_pages)

    # ---- Queue ----

    async def _enqueue(self, site: SourceSiteRecord, *, refresh: bool) -> None:
        """Enqueue ``import_site``; a failed enqueue marks the site ``failed``."""
        try:
            await queue_utils.queue_import_site(
                site["id"],
                refresh=refresh,
                database_name=self._repo.database_name,
                priority=self._settings.priorities.background,
            )
        except Exception:
            # The row says the run never started, instead of "queued" forever.
            self._repo.update(
                site["id"],
                {"status": SiteStatus.FAILED, "error_message": "Could not queue the site import."},
            )
            raise


__all__ = ["SitesService"]
