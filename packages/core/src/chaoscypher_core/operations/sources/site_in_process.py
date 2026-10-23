# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Run a site import in this process, without the queue: the CLI and the MCP server.

The worker runs ``import_site`` from the operations queue, and each page it
uploads has its indexing queued. A process with no queue (``chaoscypher
source add --site``, the MCP server's ``add_site``) runs the same operation,
:func:`handle_import_site`, here: discovery, every page fetched and staged
through the URL import path, a changed page re-imported in place, an unlisted
one marked gone. With no indexing queue the new and changed pages are left
``pending``; the caller then runs its own pipeline over
:func:`pending_site_pages`, the way it runs it over any upload of its own.
A run that reaches ``web.site_run_max_seconds`` continues here, in a loop,
where the worker would queue its continuation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.operations.sources.processing.validators import SourceFileValidators
from chaoscypher_core.operations.sources.site_import_handler import handle_import_site
from chaoscypher_core.services.sources.management.service import SourceProcessingService
from chaoscypher_core.services.sources.web.site_urls import url_key


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.ports.search import SearchRepositoryProtocol
    from chaoscypher_core.ports.storage_sites import SitePageRecord, SourceSiteRecord
    from chaoscypher_core.services.sources.web import CrawlThrottle
    from chaoscypher_core.settings import EngineSettings


class _FixedConfig:
    """The config manager ``upload_file`` reads: the app settings, for one database."""

    def __init__(self, settings: Any) -> None:
        """Hold the settings to hand out."""
        self._settings = settings

    def get_settings(self) -> Any:
        """The settings (``current_database``, ``database_dir``, ``batching``)."""
        return self._settings


def _settings_for(database_name: str) -> Any:
    """The app settings with ``current_database`` set to the database the site lives in."""
    settings = get_settings()
    if settings.current_database == database_name:
        return settings
    return settings.model_copy(update={"current_database": database_name})


#: The page counts a run reports, summed over a run and its continuations.
_COUNTS = ("added", "updated", "unchanged", "failed", "busy", "unreachable", "gone")


async def run_site_import(
    storage: SqliteAdapter,
    *,
    site_id: str,
    database_name: str,
    refresh: bool = False,
    search_repository: SearchRepositoryProtocol | None = None,
    engine_settings: EngineSettings | None = None,
    throttle: CrawlThrottle | None = None,
    settings: Any = None,
) -> dict[str, Any]:
    """Run ``import_site`` for one site in this process; new and changed pages stay ``pending``.

    Args:
        storage: The SQLite adapter the site and its pages live in.
        site_id: The site row (``site_runs.create_site_row``).
        database_name: The site's database.
        refresh: A check for updates (the site has pages already); the
            pages it leaves out are the site's (``site_runs.begin_site_refresh``).
        search_repository: The search index, for a changed page's re-import.
        engine_settings: Engine settings, for a changed page's graph release.
        throttle: The site's request gate; ``None`` builds one from settings.
        settings: App settings for the upload path (``current_database``,
            ``database_dir``, ``batching``); ``None`` is the app settings
            for ``database_name``.

    Returns:
        :func:`handle_import_site`'s result: ``{"status": "ok", "method",
        "pages", "added", "updated", ...}`` (the counts summed over the run's
        continuations) or ``{"status": "error", "error"}``.
    """
    service = SourceProcessingService(
        source_manager=storage,
        operations_manager=None,  # no queue here: pages stay pending
        config_manager=_FixedConfig(settings or _settings_for(database_name)),
        validators=SourceFileValidators(source_manager=storage, llm_provider=None),
    )
    totals = dict.fromkeys(_COUNTS, 0)
    while True:
        result = await handle_import_site(
            {"site_id": site_id, "refresh": refresh},
            source_processing_service=service,
            search_repository=search_repository,
            engine_settings=engine_settings,
            metadata={"database_name": database_name},
            throttle=throttle,
            requeue=False,
        )
        for key in _COUNTS:
            totals[key] += int(result.get(key, 0))
        if result.get("status") != "continued":
            break
    if result.get("status") != "ok":
        return result
    # "gone" is the last pass's count of unlisted pages, not a sum.
    return {**result, **totals, "gone": int(result.get("gone", 0))}


def find_site(
    storage: SqliteAdapter, database_name: str, ref: str, *, page_size: int
) -> SourceSiteRecord | None:
    """The site ``ref`` names: its id, its address (any spelling of it), or its title.

    The CLI's ``source site`` commands and the MCP server's ``refresh_site``
    name a site the way a person does; an id still works.

    Args:
        storage: The SQLite adapter.
        database_name: The database to look in.
        ref: A site id, a site address, or a title (case does not matter).
        page_size: How many sites to read at a time.

    Returns:
        The newest site that matches, or ``None``.
    """
    wanted = ref.strip()
    try:
        wanted_url = url_key(wanted) if wanted.startswith(("http://", "https://")) else None
    except ValueError:
        wanted_url = None
    wanted_title = wanted.casefold()
    page = 1
    while True:
        sites, total = storage.list_sites(database_name, page=page, page_size=page_size)
        for site in sites:
            if site["id"] == wanted or (site["title"] or "").casefold() == wanted_title:
                return site
            if wanted_url is not None and url_key(site["url"]) == wanted_url:
                return site
        if not sites or page * page_size >= total:
            return None
        page += 1


def pending_site_pages(
    storage: SqliteAdapter, site_id: str, database_name: str
) -> list[SitePageRecord]:
    """The site's pages waiting for a pipeline (new, or re-staged after a change)."""
    return [
        page
        for page in storage.list_site_pages(site_id, database_name)
        if page["status"] == SourceStatus.PENDING
    ]


__all__ = ["find_site", "pending_site_pages", "run_site_import"]
