# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The MCP server's ``add_site`` and ``refresh_site``: a docs site imported in the background.

``add_site(url)`` finds the site's pages quickly (the preview's budget),
answers in plain words ("Importing https://docs.ollama.com/ (the site's
title: "Ollama"): 71 pages found by llms.txt."), and imports in the
background: Core's
``run_site_import`` runs the ``import_site`` operation in this process (the
server has no queue) and leaves each new or changed page ``pending``; each is
then indexed for search here, the way ``add_document`` indexes a file.
``refresh_site(site, exclude?)`` checks an imported site for updates the same
way; the pages the site leaves out stay out (``exclude`` replaces that list,
and the answer names it).

Answers are plain text, never ids or JSON: an agent relays them as they are.
A site's title is the site's own text (its llms.txt heading or page title),
so it is never repeated as if it were ours: it is labelled "the site's
title", quoted, and cleaned to one short inert line (``plain_title``).

Discovery and the WebScraper's extraction run CPU-bound parsing in worker
threads (``asyncio.to_thread``). That is safe under the server's event loop:
the April 2026 hang was a module's *first* import inside a worker thread
(an import-lock deadlock), and everything those threads run is imported at
module load, before the server starts (this module imports the discovery
and its parsers).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.operations.sources.site_in_process import (
    find_site,
    pending_site_pages,
    run_site_import,
)
from chaoscypher_core.operations.sources.site_runs import (
    begin_site_refresh,
    create_site_row,
    find_site_by_scope,
    name_pages,
    site_run_live,
)
from chaoscypher_core.operations.sources.url_import_options import url_upload_options
from chaoscypher_core.services.sources.web import (
    DiscoveryBudget,
    discover_site,
    pages_found,
    plain_title,
)


if TYPE_CHECKING:
    from chaoscypher_core.bootstrap import Engine
    from chaoscypher_core.ports.storage_sites import SitePageRecord


logger = structlog.get_logger(__name__)

#: The background site runs, by site id (a reference keeps each task alive).
_RUNS: dict[str, asyncio.Task[None]] = {}


async def wait_for_site_imports() -> None:
    """Wait for every background site run to finish (tests, and an orderly shutdown)."""
    while _RUNS:
        await asyncio.gather(*list(_RUNS.values()), return_exceptions=True)


def _page_size(engine: Engine) -> int:
    """How many sites a lookup reads at a time."""
    return int(engine.settings.pagination.default_page_size)


def _named(engine: Engine, url: str, title: str) -> str:
    """A site as an answer names it: its address, then its own title, labelled and quoted.

    The title is the site's text, so it is cleaned again here (one line, no
    markup or control characters, ``web.site_title_max_chars``) and never
    stands alone where an agent could read it as part of our answer.
    """
    clean = plain_title(title, engine.settings.web.site_title_max_chars).replace('"', "'")
    if not clean or clean == url:
        return url
    return f'{url} (the site\'s title: "{clean}")'


async def _index_page(engine: Engine, page: SitePageRecord) -> None:
    """Index one staged page for search; a failure marks the page, never the run."""
    from chaoscypher_core.mcp.indexing import index_source_text

    storage = engine.storage_adapter
    storage.start_indexing(page["id"])
    try:
        await index_source_text(
            engine,
            file_id=page["id"],
            file_path=page["filepath"],
            is_url=False,
            text="",
            file_type=Path(page["filepath"]).suffix.lstrip(".") or "md",
            enable_normalization=None,
        )
    except Exception as exc:
        storage.fail_indexing(page["id"], f"{type(exc).__name__}: {exc}")
        logger.warning("mcp_site_page_index_failed", source_id=page["id"], error=str(exc))


async def _run_site(engine: Engine, site_id: str, *, refresh: bool) -> None:
    """Import (or refresh) one site in this process, then index its pending pages."""
    database = engine.settings.current_database
    try:
        result = await run_site_import(
            engine.storage_adapter,
            site_id=site_id,
            database_name=database,
            refresh=refresh,
            search_repository=engine.search_repository,
            engine_settings=engine.settings,
        )
        if result.get("status") != "ok":
            return
        for page in pending_site_pages(engine.storage_adapter, site_id, database):
            await _index_page(engine, page)
    except Exception:
        logger.exception("mcp_site_run_failed", site_id=site_id)
    finally:
        _RUNS.pop(site_id, None)


def _start(engine: Engine, site_id: str, *, refresh: bool) -> None:
    """Run the site in the background (one run per site at a time)."""
    _RUNS[site_id] = asyncio.create_task(_run_site(engine, site_id, refresh=refresh))


async def add_site(engine: Engine, url: str) -> tuple[str, bool]:
    """``add_site``: find the site's pages, answer how many, import them in the background.

    Args:
        engine: The server's engine.
        url: The site's address.

    Returns:
        ``(answer, is_error)``: the plain-text answer and whether it is a refusal.
    """
    settings = engine.settings
    try:
        manifest = await discover_site(
            url, settings, budget=DiscoveryBudget.for_preview(settings.web)
        )
    except ValidationError as exc:
        return f"That address cannot be imported: {exc.message}", True
    if not manifest.pages:
        return (
            f"No pages were found at {manifest.url}. add_document imports a single page.",
            True,
        )
    database = settings.current_database
    existing = find_site_by_scope(
        engine.storage_adapter, database, manifest.url, page_size=_page_size(engine)
    )
    if existing is not None:
        name = _named(engine, existing["url"], existing["title"])
        return f"{name} is already imported; refresh_site checks it for updates.", False
    site = create_site_row(
        engine.storage_adapter,
        url=manifest.url,
        database_name=database,
        options=url_upload_options(
            extract_entities=False,  # indexed for search; extraction is the client's
            # A site is many pages: none waits on a domain confirmation.
            auto_confirm=True,
        ),
    )
    _start(engine, site["id"], refresh=False)
    logger.info("mcp_site_import_started", site_id=site["id"], pages=len(manifest.pages))
    return (
        f"Importing {_named(engine, manifest.url, manifest.title)}: {pages_found(manifest)}. "
        "The pages are fetched first, then indexed for search one by one; refresh_site "
        "checks the site for updates later.",
        False,
    )


def _left_out(engine: Engine, excluded: list[str], back: list[str]) -> str:
    """The pages a check brings back and the ones it leaves out, in words ("" when none).

    A few addresses each, then "and N more" (``web.site_pages_named_max``).
    """
    limit = engine.settings.web.site_pages_named_max
    words = ""
    if back:
        count = "1 page comes" if len(back) == 1 else f"{len(back)} pages come"
        deleted = (
            "a page deleted from the site is" if len(back) == 1 else "pages deleted from it are"
        )
        words += f" {count} back: {name_pages(back, limit=limit)} ({deleted} imported again)."
    if excluded:
        count = "1 page is" if len(excluded) == 1 else f"{len(excluded)} pages are"
        words += (
            f" {count} left out: {name_pages(excluded, limit=limit)}. "
            "refresh_site with exclude changes that list."
        )
    return words


async def refresh_site(
    engine: Engine, ref: str, exclude: list[str] | None = None
) -> tuple[str, bool]:
    """``refresh_site``: check an imported site for updates, in the background.

    Args:
        engine: The server's engine.
        ref: The site's address or title (an id works too).
        exclude: The pages to leave out from now on (replacing the site's
            list; an empty list brings every page back); ``None`` keeps it.

    Returns:
        ``(answer, is_error)``.
    """
    database = engine.settings.current_database
    site = find_site(engine.storage_adapter, database, ref, page_size=_page_size(engine))
    if site is None:
        return f"No imported site matches {ref!r}.", True
    name = _named(engine, site["url"], site["title"])
    if site_run_live(site) or site["id"] in _RUNS:
        return f"{name} is already being imported or checked.", True
    try:
        back = begin_site_refresh(engine.storage_adapter, site["id"], database, exclude=exclude)
    except ValidationError as exc:
        return exc.message, True
    _start(engine, site["id"], refresh=True)
    started = engine.storage_adapter.get_site(site["id"], database) or site
    words = _left_out(engine, started["excluded_pages"], back)
    return f"Checking {name} for updates.{words}", False


async def handle_site_tool(engine: Engine, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Dispatch ``add_site`` / ``refresh_site`` (the server's ``call_tool`` routes here)."""
    if name == "add_site":
        url = str(args.get("url") or "").strip()
        if not url:
            return "add_site needs the site's address (url).", True
        return await add_site(engine, url)
    ref = str(args.get("site") or "").strip()
    if not ref:
        return "refresh_site needs the site's address or title (site).", True
    exclude = args.get("exclude")
    if exclude is not None and not (
        isinstance(exclude, list) and all(isinstance(u, str) for u in exclude)
    ):
        return "refresh_site's exclude is a list of page addresses.", True
    return await refresh_site(engine, ref, exclude)


SITE_TOOLS = frozenset({"add_site", "refresh_site"})
"""The site tools the server dispatches to :func:`handle_site_tool`."""


__all__ = [
    "SITE_TOOLS",
    "add_site",
    "handle_site_tool",
    "refresh_site",
    "wait_for_site_imports",
]
