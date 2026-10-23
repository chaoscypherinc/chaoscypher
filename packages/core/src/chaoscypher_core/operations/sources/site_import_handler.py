# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Site import handler: a docs site's pages discovered and imported (spec 2026-10-22).

Background handler for ``OP_IMPORT_SITE`` (queue: ``operations``). For one
``source_sites`` row it runs ``discover_site_gate`` on the site's address, then
imports each page through :func:`import_url_page`, the single-URL import's own
path (placeholder, the shared ``fetch_page``, the upload pipeline), with the
site's id and its stored upload options under the site rules
(:func:`site_page_options`: no page waits for a domain confirmation, and
``skip_duplicates`` never takes a page from its site). Every request to the
site, discovery and pages alike, goes through one :class:`CrawlThrottle`,
raised to the robots.txt ``Crawl-delay`` the discovery read. A page whose final
address after redirects leaves the site's scope or is disallowed by its
robots.txt is refused (the discovery's :class:`SiteGate`).

A page that fails, the unexpected errors included, is recorded on its own row
and never stops the site; the site row records the run itself: ``importing``
(or ``refreshing``) while it runs, ``imported`` when every page was handed on,
``failed`` when the run stopped (the address refused, nothing found, an
unexpected error outside a page, a cancel). The page count is written as soon
as discovery ends, so progress reads against the whole site.

A run is resumable: ``source_sites.run_cursor`` counts the discovered pages
the open pass has handled, written after each one. A run cut off part-way
(the operations timeout's cancel, a crash) leaves it, and the next run, a
refresh included, starts there instead of fetching every page again. A run
that has worked ``web.site_run_max_seconds`` saves its place and queues the
rest as a new run (``requeue``; a process with no queue loops instead).

A page the site already has (matched on :func:`page_identity`, so ``/a.md``
from llms.txt and ``/a`` from a sitemap are one page) is fetched and compared
by content hash: unchanged, it is left alone; changed, it is re-imported in
place (:func:`reimport_page_in_place`: the existing graph release, then the new
content through the pipeline); still processing, it waits for the next check.
A page the discovery no longer lists is marked gone (``sources.site_gone_at``),
never deleted, and unmarked when listed again; a capped discovery, or one by a
different method than the site's last (a sitemap after llms.txt), marks none.
A page whose earlier fetch failed is tried again from scratch.

The pages the site leaves out (``source_sites.excluded_pages``: unticked at
import, the list a refresh passed last, the pages deleted from the site) are
skipped on every run; the surfaces set them (``site_runs.create_site_row``,
``begin_site_refresh``), so a continuation and a resumed run read the same list.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.models import SiteStatus, SourceErrorStage, SourceStatus
from chaoscypher_core.operations import queue_utils
from chaoscypher_core.operations.sources.page_reimport import reimport_page_in_place
from chaoscypher_core.operations.sources.site_runs import (
    page_key,
    site_leaves_out,
    site_page_options,
)
from chaoscypher_core.operations.sources.url_fetch_handler import (
    import_url_page,
    page_upload_problem,
)
from chaoscypher_core.services.sources.web import (
    CrawlThrottle,
    FetchedPage,
    SiteGate,
    SiteManifest,
    discover_site_gate,
    fetch_page,
)


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.ports.search import SearchRepositoryProtocol
    from chaoscypher_core.ports.storage_sites import SitePageRecord, SourceSiteRecord
    from chaoscypher_core.services.sources import SourceProcessingService
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


def _is_failed_fetch(page: SitePageRecord) -> bool:
    """A placeholder whose fetch failed: it holds no content and no graph rows."""
    return (
        page["status"] == SourceStatus.ERROR and page["error_stage"] == SourceErrorStage.URL_FETCH
    )


#: Statuses the pipeline is done with (``INDEXED`` too, when extraction is off).
_SETTLED = frozenset(
    {SourceStatus.COMMITTED, SourceStatus.AWAITING_CONFIRMATION, SourceStatus.ERROR}
)


def _is_settled(page: SitePageRecord) -> bool:
    """Whether the pipeline is done with the page, so a re-import cannot race a running stage.

    Settled: committed, parked for confirmation, in error, or indexed when it
    was imported without extraction. Anything else is still being processed
    and is left for the next check.
    """
    status = page["status"]
    if status in _SETTLED:
        return True
    return status == SourceStatus.INDEXED and not page["auto_analyze"]


class _SiteRun:
    """One run over a site: discovery, then each page added, checked, or left as it is."""

    def __init__(
        self,
        site: SourceSiteRecord,
        *,
        source_processing_service: SourceProcessingService,
        search_repository: SearchRepositoryProtocol | None,
        engine_settings: EngineSettings | None,
        database_name: str,
        task_id: str | None,
        throttle: CrawlThrottle,
        gate: SiteGate,
        started: float,
    ) -> None:
        """Hold the run's site, services, throttle and gate; see :func:`handle_import_site`."""
        self.site = site
        self.service = source_processing_service
        self.storage: SqliteAdapter = source_processing_service.source_manager
        self.search_repository = search_repository
        self.engine_settings = engine_settings
        self.database_name = database_name
        self.task_id = task_id
        self.throttle = throttle
        self.gate = gate
        self.started = started
        self.settings = get_settings()
        self.options = site_page_options(site["options"])
        self.counts = dict.fromkeys(
            ("added", "updated", "unchanged", "failed", "busy", "unreachable", "gone"), 0
        )

    async def fetch(self, url: str) -> FetchedPage:
        """``fetch_page`` under the site's throttle, refusing a page that left the site.

        Raises:
            ValidationError: The page's final address, after its redirects, is
                outside the site's scope or disallowed by its robots.txt (the
                URL import records it as that page's failure).
        """
        async with self.throttle.slot():
            page = await fetch_page(url, self.settings)
        if page.error is None and not self.gate.admits(page.final_url):
            logger.warning("site_page_left_the_site", url=url, final_url=page.final_url)
            msg = (
                f"The page redirected to {page.final_url}, outside the site "
                "or disallowed by its robots.txt."
            )
            raise ValidationError(msg, field="url")
        return page

    async def add_page(self, url: str) -> None:
        """Import a page the site did not have, through the single-URL import's path."""
        result = await import_url_page(
            url,
            dict(self.options),
            source_processing_service=self.service,
            database_name=self.database_name,
            task_id=self.task_id,
            site_id=self.site["id"],
            fetch=self.fetch,
        )
        self.counts["failed" if result.get("status") == "error" else "added"] += 1

    async def check_page(self, page: SitePageRecord, url: str) -> None:
        """Fetch a page the site has; re-import it in place only when its text changed.

        A page that cannot be fetched now keeps what it has (``unreachable``);
        one still in the pipeline is left for the next check (``busy``).
        """
        if not _is_settled(page):
            self.counts["busy"] += 1
            return
        try:
            fetched = await self.fetch(url)
        except ValidationError as exc:
            self._unreachable(page, url, f"Failed to fetch URL: {exc.message}")
            return
        problem = page_upload_problem(fetched, url, self.settings.batching.max_upload_bytes)
        if problem is not None:
            self._unreachable(page, url, problem)
            return
        if fetched.content_hash == page["content_hash"]:
            self.counts["unchanged"] += 1
            return
        await reimport_page_in_place(
            page["id"],
            fetched,
            storage=self.storage,
            operations_manager=self.service.operations_manager,
            search_repository=self.search_repository,
            engine_settings=self.engine_settings,
            settings=self.settings,
            database_name=self.database_name,
        )
        self.counts["updated"] += 1

    def _unreachable(self, page: SitePageRecord, url: str, problem: str) -> None:
        """Log a page that could not be fetched now; it keeps its content."""
        logger.warning("site_page_unreachable", source_id=page["id"], url=url, error=problem)
        self.counts["unreachable"] += 1

    async def handle_page(self, url: str, page: SitePageRecord | None) -> None:
        """Add, check or retry one listed page; an unexpected error is that page's failure."""
        try:
            if page is not None and not _is_failed_fetch(page):
                await self.check_page(page, url)
                return
            if page is not None:
                # A failed fetch is tried again from scratch; its placeholder
                # never held content or graph rows, so the row just goes.
                self.storage.delete_source(page["id"], self.database_name)
            await self.add_page(url)
        except Exception as exc:
            self._page_failed(page, url, exc)

    def _page_failed(self, page: SitePageRecord | None, url: str, exc: Exception) -> None:
        """Record an unexpected error on its page (when it still exists) and go on."""
        error = f"Unexpected error: {exc}"
        logger.warning(
            "site_page_failed",
            site_id=self.site["id"],
            url=url,
            source_id=page["id"] if page is not None else None,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        self.counts["failed"] += 1
        if page is not None:
            # The page may be gone (deleted during the run): nothing to record on.
            with contextlib.suppress(Exception):
                self.storage.update_file(page["id"], self.database_name, {"error_message": error})

    def out_of_time(self) -> bool:
        """Whether the run has worked ``web.site_run_max_seconds`` (on the throttle's clock)."""
        return self.throttle.now() - self.started >= self.settings.web.site_run_max_seconds

    def save_cursor(self, handled: int) -> None:
        """Record how many of the discovered pages the open pass has handled."""
        self.storage.update_site(self.site["id"], self.database_name, {"run_cursor": handled})

    async def run_pages(self, manifest: SiteManifest, *, previous_method: str | None) -> bool:
        """Handle each listed page from the cursor on, except the ones the site leaves out.

        A page the site has and now leaves out keeps what it has: it is not
        checked for changes (nor marked gone while the site still lists it).
        The list is read again before each page (:meth:`leaves_out`), so a
        page the user deletes while the run is under way is not added back.

        Returns:
            True when the pass reached the last page; False when it stopped
            on ``web.site_run_max_seconds`` with pages left (the cursor says
            where the next run starts).
        """
        existing = {
            key: page
            for page in self.storage.list_site_pages(self.site["id"], self.database_name)
            if page["origin_url"] and (key := page_key(page["origin_url"])) is not None
        }
        pages = manifest.pages
        start = min(self.site["run_cursor"] or 0, len(pages))
        if start:
            logger.info("site_run_resumed", site_id=self.site["id"], cursor=start, pages=len(pages))
        handled: set[str] = set()
        for index in range(start, len(pages)):
            # Every run handles at least one page, so a slow discovery cannot
            # make a run that only ever continues.
            if index > start and self.out_of_time():
                self.save_cursor(index)
                return False
            key = page_key(pages[index].url)
            if key is not None and key not in handled and not self.leaves_out(pages[index].url):
                handled.add(key)
                await self.handle_page(pages[index].url, existing.get(key))
            self.save_cursor(index + 1)
        self.mark_gone(manifest, existing, previous_method)
        return True

    def leaves_out(self, url: str) -> bool:
        """Whether the site leaves the page out, by its list as it is now (not at the start)."""
        current = self.storage.get_site(self.site["id"], self.database_name) or self.site
        return site_leaves_out(current, url)

    def mark_gone(
        self,
        manifest: SiteManifest,
        existing: dict[str, SitePageRecord],
        previous_method: str | None,
    ) -> None:
        """Mark the pages the site no longer lists as gone (never deleted); clear the relisted.

        A capped discovery did not list every page, and one by another method
        than the site's last lists its pages another way (a sitemap may leave
        out what llms.txt listed): neither marks anything gone.
        """
        listed = {page_key(p.url) for p in manifest.pages}
        back = [p["id"] for k, p in existing.items() if k in listed and p["site_gone_at"]]
        self.storage.set_site_pages_gone(back, self.database_name, None)
        if manifest.capped:
            logger.info("site_gone_marking_skipped_capped", site_id=self.site["id"])
            return
        if previous_method is not None and previous_method != manifest.method.value:
            logger.info(
                "site_gone_marking_skipped_method_changed",
                site_id=self.site["id"],
                previous=previous_method,
                method=manifest.method.value,
            )
            return
        unlisted = [p for k, p in existing.items() if k not in listed]
        newly = [p["id"] for p in unlisted if p["site_gone_at"] is None]
        self.storage.set_site_pages_gone(newly, self.database_name, datetime.now(UTC))
        self.counts["gone"] = len(unlisted)


def _fail_site(storage: SqliteAdapter, site_id: str, database_name: str, error: str) -> None:
    """Record that the run stopped: the site ``failed`` with the reason, checked now.

    The cursor stays, so the next run resumes where this one stopped.
    """
    storage.update_site(
        site_id,
        database_name,
        {
            "status": SiteStatus.FAILED,
            "error_message": error,
            "last_checked_at": datetime.now(UTC),
        },
    )


async def _continue_later(
    run: _SiteRun,
    manifest: SiteManifest,
    *,
    refresh: bool,
    requeue: bool,
) -> dict[str, Any]:
    """Queue the rest of the site as a new run (the cursor says where it starts)."""
    storage, site_id, database_name = run.storage, run.site["id"], run.database_name
    storage.update_site(site_id, database_name, {"status": SiteStatus.QUEUED})
    if requeue:
        try:
            await queue_utils.queue_import_site(
                site_id,
                refresh=refresh,
                database_name=database_name,
                priority=run.settings.priorities.background,
            )
        except Exception:
            error = "Could not queue the rest of the site import."
            _fail_site(storage, site_id, database_name, error)
            logger.exception("site_import_continuation_not_queued", site_id=site_id)
            return {"status": "error", "site_id": site_id, "error": error}
    logger.info("site_import_continues", site_id=site_id, cursor=run.site["run_cursor"])
    return {
        "status": "continued",
        "site_id": site_id,
        "method": manifest.method.value,
        "pages": len(manifest.pages),
        **run.counts,
    }


async def handle_import_site(
    data: dict[str, Any],
    *,
    source_processing_service: SourceProcessingService,
    search_repository: SearchRepositoryProtocol | None = None,
    engine_settings: EngineSettings | None = None,
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
    throttle: CrawlThrottle | None = None,
    requeue: bool = True,
) -> dict[str, Any]:
    """Discover a site's pages and import them (``OP_IMPORT_SITE``).

    Args:
        data: ``{"site_id": str, "refresh": bool}``; the pages to leave out
            are the site's own (``excluded_pages``).
        source_processing_service: Worker-wired source processing service
            (its ``source_manager`` is the SQLite adapter).
        search_repository: The search index, for the refresh's re-import.
        engine_settings: Engine settings, for the refresh's graph release.
        metadata: Queue task metadata; must contain ``database_name``.
        task_id: Queue task id, for tracing.
        throttle: The site's request gate; ``None`` builds one from
            ``web.crawl_delay_seconds`` and ``web.crawl_concurrency``.
        requeue: When the run stops on ``web.site_run_max_seconds``, queue
            the rest as a new ``OP_IMPORT_SITE`` task; a process with no queue
            passes False and runs again itself.

    Returns:
        ``{"status": "ok", "site_id", "method", "pages", "added", "updated",
        "unchanged", "failed", "busy", "unreachable", "gone"}`` (page counts:
        new pages imported, changed ones re-imported in place, unchanged,
        failed pages, pages still processing and left alone, pages that could
        not be fetched and kept their content, pages no longer listed), the
        same with ``"status": "continued"`` when the rest was handed to a new
        run, or ``{"status": "error", "site_id", "error"}`` when the run stopped.
    """
    if metadata is None or "database_name" not in metadata:
        msg = "Site import task metadata missing required 'database_name' key"
        # nosemgrep: cc-045-bare-stdlib-raise-in-core - programmer error: metadata contract violation; queue consumer must populate database_name before dispatch
        raise ValueError(msg)
    database_name = metadata["database_name"]
    site_id = data["site_id"]
    storage: SqliteAdapter = source_processing_service.source_manager
    site = storage.get_site(site_id, database_name)
    if site is None:
        logger.warning("site_import_site_missing", site_id=site_id, task_id=task_id)
        return {"status": "error", "site_id": site_id, "error": "site not found"}

    settings = get_settings()
    refresh = bool(data.get("refresh"))
    if throttle is None:
        throttle = CrawlThrottle(
            delay_seconds=settings.web.crawl_delay_seconds,
            concurrency=settings.web.crawl_concurrency,
        )
    started = throttle.now()
    storage.update_site(
        site_id,
        database_name,
        {
            "status": SiteStatus.REFRESHING if refresh else SiteStatus.IMPORTING,
            "error_message": None,
        },
    )
    logger.info("site_import_started", site_id=site_id, refresh=refresh, task_id=task_id)
    try:
        try:
            manifest, gate = await discover_site_gate(site["url"], settings, throttle=throttle)
        except ValidationError as exc:
            _fail_site(storage, site_id, database_name, exc.message)
            logger.warning("site_import_refused", site_id=site_id, error=exc.message)
            return {"status": "error", "site_id": site_id, "error": exc.message}
        if not manifest.pages:
            error = "No pages were found under this address."
            _fail_site(storage, site_id, database_name, error)
            logger.warning("site_import_found_nothing", site_id=site_id, url=site["url"])
            return {"status": "error", "site_id": site_id, "error": error}
        throttle.raise_delay(manifest.crawl_delay_seconds)
        # Progress reads against the whole site from the first page on.
        storage.update_site(
            site_id,
            database_name,
            {"page_count": len(manifest.pages), "title": manifest.title},
        )

        run = _SiteRun(
            site,
            source_processing_service=source_processing_service,
            search_repository=search_repository,
            engine_settings=engine_settings,
            database_name=database_name,
            task_id=task_id,
            throttle=throttle,
            gate=gate,
            started=started,
        )
        finished = await run.run_pages(manifest, previous_method=site["discovery_method"])
        if not finished:
            run.site = storage.get_site(site_id, database_name) or site
            return await _continue_later(run, manifest, refresh=refresh, requeue=requeue)
        storage.update_site(
            site_id,
            database_name,
            {
                "status": SiteStatus.IMPORTED,
                "title": manifest.title,
                "discovery_method": manifest.method.value,
                "page_count": len(manifest.pages),
                "last_checked_at": datetime.now(UTC),
                "error_message": None,
                "run_cursor": None,
            },
        )
    except asyncio.CancelledError:
        # Synchronous on purpose: an ``await`` in a cancelled coroutine re-raises.
        with contextlib.suppress(Exception):
            _fail_site(storage, site_id, database_name, "Site import cancelled")
        raise
    except Exception as exc:
        with contextlib.suppress(Exception):
            _fail_site(storage, site_id, database_name, f"Unexpected error: {exc}")
        raise
    logger.info(
        "site_import_finished",
        site_id=site_id,
        method=manifest.method.value,
        pages=len(manifest.pages),
        **run.counts,
    )
    return {
        "status": "ok",
        "site_id": site_id,
        "method": manifest.method.value,
        "pages": len(manifest.pages),
        **run.counts,
    }


__all__ = ["handle_import_site"]
