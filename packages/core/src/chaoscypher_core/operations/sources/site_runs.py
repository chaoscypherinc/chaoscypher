# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The rules every surface applies to a site and its runs: one source for each.

The web UI's routes (Cortex), the command line and the MCP server all import
a docs site, refresh it and delete it. What they must agree on lives here:

- :func:`site_page_options`: the upload options every page of a site is
  imported with, whatever the client sent (no page waits for a domain
  confirmation; ``skip_duplicates`` never hands the user's own source to the
  site).
- :func:`site_run_live` / :func:`refuse_while_running`: whether a run of the
  site is really queued or under way. A run is live only while its status
  says so *and* it started within the operations worker's timeout: a worker
  killed mid-run (no cancel, no error) leaves the status behind, and a stale
  run must not lock the refresh that resumes it, nor the delete.
- :func:`fail_interrupted_runs`: at worker startup, a site whose run was left
  queued or under way with no live task is marked ``failed`` with a message
  that says to check for updates.
- :func:`find_site_by_scope`: the site already imported for an address (the
  same scheme, host and path scope in the same database), so no surface
  imports one site twice.
- :func:`create_site_row` / :func:`begin_site_refresh`: how every surface
  starts a site's import and a check for updates.
- :func:`exclusion_keys` / :func:`leave_out_deleted_page`: the pages a site
  leaves out (``source_sites.excluded_pages``). The import stores the pages
  the user unticked; a refresh with no list of its own keeps them out, and a
  refresh that passes one replaces them (that is how a page comes back); a
  page the user deletes from the site joins them, so a refresh does not bring
  back what was deleted. They are stored as page identities
  (:func:`page_key`), so another spelling of an address still matches.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import structlog

from chaoscypher_core.exceptions import ConflictError, ValidationError
from chaoscypher_core.models import SiteStatus
from chaoscypher_core.services.sources.web.site_urls import SiteScope, page_identity
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.ports.storage_sites import SiteStorageProtocol, SourceSiteRecord


logger = structlog.get_logger(__name__)

#: What a site whose run was killed says (the refresh resumes from its cursor).
SITE_RUN_INTERRUPTED_MESSAGE = (
    "The import was interrupted (the worker stopped). Check for updates to resume it."
)


def site_page_options(options: Mapping[str, Any] | None) -> dict[str, Any]:
    """The upload options a site's every page is imported with, whatever the client sent.

    - ``auto_confirm`` is always on: a site is many pages, and none of them
      is parked one by one for domain confirmation.
    - ``skip_duplicates`` is always off: a page whose text is already a
      source is still imported as the site's own page. Attaching the
      existing source to the site instead would let "delete the site and its
      pages" delete the user's own source, and a refresh re-import it.

    Args:
        options: The stored or requested upload options (``url_upload_options``).

    Returns:
        A copy with the two site rules applied.
    """
    return {**dict(options or {}), "auto_confirm": True, "skip_duplicates": False}


def run_stale_after_seconds() -> int:
    """How old a run may be and still be live: the operations worker's deadline plus margin.

    The deadline the worker enforces (``workers.yaml``, floored at
    ``timeouts.operations_worker_default``), plus the reconciler's safety
    margin: a site run is never older than that while its worker lives,
    because a long site saves its place and continues as a new run before
    ``web.site_run_max_seconds``.
    """
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.constants import QUEUE_OPERATIONS
    from chaoscypher_core.queue.worker_timeouts import (
        reconciler_cutoff_seconds,
        resolve_effective_worker_timeout,
    )

    default = get_settings().timeouts.operations_worker_default
    cutoff = reconciler_cutoff_seconds(
        resolve_effective_worker_timeout(QUEUE_OPERATIONS, default=default)
    )
    return int(cutoff if cutoff is not None else default)


def site_run_live(
    site: SourceSiteRecord,
    *,
    now: datetime | None = None,
    stale_after_seconds: int | None = None,
) -> bool:
    """Whether a run of *site* is queued or under way and not stale.

    Args:
        site: The site row.
        now: The time to judge at (UTC); ``None`` is now.
        stale_after_seconds: How old a live run may be;
            ``None`` is :func:`run_stale_after_seconds`.

    Returns:
        True when the status is queued, importing or refreshing and the run
        started less than ``stale_after_seconds`` ago. A run with no start
        time is stale.
    """
    if not SiteStatus(site["status"]).in_progress:
        return False
    started = site["run_started_at"]
    if started is None:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    limit = run_stale_after_seconds() if stale_after_seconds is None else stale_after_seconds
    age = ((now or datetime.now(UTC)) - started).total_seconds()
    return age < limit


def refuse_while_running(site: SourceSiteRecord, action: str) -> None:
    """Refuse *action* (a refresh, a delete) while a live run of the site exists.

    Raises:
        ConflictError: A run of the site is queued or under way (stale runs
            do not count; see :func:`site_run_live`).
    """
    if site_run_live(site):
        msg = f"{site['title']} is being imported or checked for updates; {action} must wait."
        raise ConflictError(msg, details={"site_id": site["id"], "status": site["status"]})


def fail_interrupted_runs(
    storage: SiteStorageProtocol,
    database_name: str,
    *,
    has_live_task: Callable[[SourceSiteRecord], bool],
) -> list[str]:
    """Mark ``failed`` every site left queued or under way whose run has no live task.

    Called at worker startup (nothing of this worker runs yet) and by the
    upgrade recovery. The site keeps its cursor, so the next check for
    updates resumes where the run stopped.

    Args:
        storage: The site storage.
        database_name: The database to look in.
        has_live_task: Whether a queue task still backs the site's run
            (a queued task waiting for this worker).

    Returns:
        The ids of the sites marked failed.
    """
    failed: list[str] = []
    for site in storage.list_running_sites(database_name):
        if has_live_task(site):
            continue
        storage.update_site(
            site["id"],
            database_name,
            {"status": SiteStatus.FAILED, "error_message": SITE_RUN_INTERRUPTED_MESSAGE},
        )
        failed.append(site["id"])
    if failed:
        logger.warning("site_runs_interrupted", database_name=database_name, site_ids=failed)
    return failed


def find_site_by_scope(
    storage: SiteStorageProtocol, database_name: str, url: str, *, page_size: int
) -> SourceSiteRecord | None:
    """The site already imported for *url*: the same scope in the same database.

    Two addresses share a site when :class:`SiteScope` reads them the same
    (scheme, host and port, path prefix): ``https://docs.example.com``,
    ``https://docs.example.com/`` and ``https://docs.example.com/index.html``
    are one site.

    Args:
        storage: The site storage.
        database_name: The database to look in.
        url: The address a surface was asked to import.
        page_size: How many sites to read at a time.

    Returns:
        The newest matching site, or ``None`` (also for an unparsable address).
    """
    try:
        wanted = SiteScope.from_url(url)
    except ValueError:
        return None
    page = 1
    while True:
        sites, total = storage.list_sites(database_name, page=page, page_size=page_size)
        for site in sites:
            try:
                if SiteScope.from_url(site["url"]) == wanted:
                    return site
            except ValueError:
                continue
        if not sites or page * page_size >= total:
            return None
        page += 1


def page_key(url: str) -> str | None:
    """The page an address names (:func:`page_identity`), or ``None`` when it names none.

    ``None`` for an address that is not an absolute http(s) URL or cannot be
    parsed (a bad port). Site runs match pages on it, and the pages a site
    leaves out are stored as it, so ``/faq``, ``/faq/`` and ``/faq.md`` are
    one page.
    """
    try:
        key = page_identity(url)
    except ValueError:
        return None
    parts = urlsplit(key)
    return key if parts.scheme in ("http", "https") and parts.netloc else None


def site_page_key(url: str, scope: SiteScope) -> str | None:
    """The page of the site *scope* that *url* names, spelled with the site's scheme.

    The scheme is the site's whatever the address says: an ``http://``
    spelling of an ``https://`` site's page names that page, and a site
    entered as ``http://`` whose pages answer on ``https://`` still matches
    them. ``None`` when *url* names no page, or a page of another host or
    outside the site's path.
    """
    key = page_key(url)
    if key is None:
        return None
    parts = urlsplit(key)
    if not replace(scope, scheme=parts.scheme).contains(key):
        return None
    return urlunsplit((scope.scheme, parts.netloc, parts.path, parts.query, ""))


def name_pages(urls: Sequence[str], *, limit: int) -> str:
    """A few page addresses in words: "a, b, c and 4 more" ("" for none).

    Every surface names the pages a site leaves out or brings back this way
    (``web.site_pages_named_max``), so a 500-page list is never spelled out.
    """
    shown = ", ".join(urls[:limit])
    rest = len(urls) - limit
    return f"{shown} and {rest} more" if rest > 0 else shown


def exclusion_keys(urls: Iterable[str] | None, *, site_url: str) -> list[str] | None:
    """The pages to leave out, as the site stores them: sorted, unique page identities.

    Args:
        urls: The addresses a client asked to leave out; ``None`` when it
            sent no list (the site's stored one stands).
        site_url: The site's address; every entry must name one of its pages.

    Returns:
        ``None`` for ``None``; otherwise the :func:`site_page_key` of each
        address (an empty list leaves nothing out).

    Raises:
        ValidationError: More addresses than ``web.site_max_pages``, or an
            entry that names no page of the site (no scheme, a bare path,
            another host, outside its path): refused rather than dropped, so
            a typo never empties the list and brings every page back.
    """
    if urls is None:
        return None
    wanted = list(urls)
    from chaoscypher_core.app_config import get_settings

    web = get_settings().web
    if len(wanted) > web.site_max_pages:
        msg = f"A site leaves out at most {web.site_max_pages} pages (web.site_max_pages)."
        raise ValidationError(msg, field="exclude")
    scope = SiteScope.from_url(site_url)
    keys = {url: site_page_key(url, scope) for url in wanted}
    bad = [url for url, key in keys.items() if key is None]
    if bad:
        named = name_pages(bad, limit=web.site_pages_named_max)
        msg = (
            f"Not pages of {site_url} (give each page's full address, "
            f"like {scope.origin}{scope.path_prefix}/page): {named}"
        )
        raise ValidationError(msg, field="exclude")
    return sorted({key for key in keys.values() if key is not None})


def site_leaves_out(site: SourceSiteRecord, url: str | None) -> bool:
    """Whether *url* names a page the site leaves out (``excluded_pages``)."""
    if not url or not site["excluded_pages"]:
        return False
    key = site_page_key(url, SiteScope.from_url(site["url"]))
    return key is not None and key in site["excluded_pages"]


def create_site_row(
    storage: SiteStorageProtocol,
    *,
    url: str,
    database_name: str,
    options: Mapping[str, Any],
    exclude: Iterable[str] | None = None,
) -> SourceSiteRecord:
    """Create a queued site for ``url`` (named by its address until discovery names it).

    Every surface creates a site here: the web UI's route, the command line
    and the MCP server.

    Args:
        storage: The site storage.
        url: The site address (already past the strict SSRF check).
        database_name: The database the site's pages land in.
        options: The pages' upload options (``url_upload_options``), stored
            under the site rules (:func:`site_page_options`).
        exclude: Pages to leave out (the ones unticked in the preview),
            stored as :func:`exclusion_keys`; every refresh keeps them out.

    Returns:
        The new site row.

    Raises:
        ValidationError: More pages to leave out than ``web.site_max_pages``.
    """
    return storage.create_site(
        site_id=generate_id(),
        database_name=database_name,
        url=url,
        title=url,
        status=SiteStatus.QUEUED,
        options=site_page_options(options),
        excluded_pages=exclusion_keys(exclude, site_url=url) or [],
    )


def begin_site_refresh(
    storage: SiteStorageProtocol,
    site_id: str,
    database_name: str,
    *,
    exclude: Iterable[str] | None = None,
) -> list[str]:
    """Start a check for updates: the site queued, and its pages to leave out set.

    Every surface starts a refresh here (the web UI's route, the command line,
    the MCP server), after its own check that no live run exists.

    Args:
        storage: The site storage.
        site_id: The site to refresh.
        database_name: Its database.
        exclude: ``None`` keeps the pages the site leaves out; a list
            replaces them (an empty one brings every page back).

    Returns:
        The pages the new list brings back (left out before, not now), so
        the surface can name them: a deleted page among them is imported
        again. Empty when the list stays or only grows.

    Raises:
        NotFoundError: No such site in ``database_name``.
        ValidationError: More pages to leave out than ``web.site_max_pages``,
            or an entry that names no page of the site; nothing is written.
    """
    from chaoscypher_core.exceptions import NotFoundError

    site = storage.get_site(site_id, database_name)
    if site is None:
        raise NotFoundError("Site", site_id)
    updates: dict[str, Any] = {"status": SiteStatus.QUEUED, "error_message": None}
    keys = exclusion_keys(exclude, site_url=site["url"])
    back: list[str] = []
    if keys is not None:
        updates["excluded_pages"] = keys
        back = sorted(set(site["excluded_pages"]) - set(keys))
    storage.update_site(site_id, database_name, updates)
    return back


def leave_out_deleted_page(
    storage: SiteStorageProtocol, source: Mapping[str, Any], database_name: str
) -> None:
    """Add a deleted page of a site to the pages the site leaves out.

    Called by the source delete (the one the web UI, the command line and the
    MCP server run) for a source with a ``site_id``, so a refresh does not
    bring back a page the user deleted. A refresh with a list of its own
    brings it back. A source outside any site, or a site already gone, is
    left alone.

    Args:
        storage: The site storage.
        source: The source row being deleted (``site_id``, ``origin_url``).
        database_name: Its database.
    """
    site_id = source.get("site_id")
    if not site_id:
        return
    site = storage.get_site(str(site_id), database_name)
    if site is None:
        return
    key = site_page_key(str(source.get("origin_url") or ""), SiteScope.from_url(site["url"]))
    if key is None or key in site["excluded_pages"]:
        return
    storage.update_site(
        site["id"], database_name, {"excluded_pages": sorted([*site["excluded_pages"], key])}
    )
    logger.info("site_page_left_out_on_delete", site_id=site["id"], page=key)


__all__ = [
    "SITE_RUN_INTERRUPTED_MESSAGE",
    "begin_site_refresh",
    "create_site_row",
    "exclusion_keys",
    "fail_interrupted_runs",
    "find_site_by_scope",
    "leave_out_deleted_page",
    "name_pages",
    "page_key",
    "refuse_while_running",
    "run_stale_after_seconds",
    "site_leaves_out",
    "site_page_key",
    "site_page_options",
    "site_run_live",
]
