# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Import a docs site from the command line: ``source add URL --site`` and ``source site``.

The CLI runs in-process, with no queue: Core's ``run_site_import`` runs the
same ``import_site`` operation the worker runs (discovery, every page through
the URL import path, a changed page re-imported in place, an unlisted page
marked gone) and leaves the new and changed pages ``pending``; the CLI then
runs each through its own pipeline (``SourcePipeline``), as ``source add``
does for a file, and prints a line per page.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.operations.sources.site_in_process import (
    find_site,
    pending_site_pages,
    run_site_import,
)
from chaoscypher_core.operations.sources.site_runs import (
    create_site_row,
    find_site_by_scope,
    name_pages,
    site_run_live,
)
from chaoscypher_core.operations.sources.url_import_options import url_upload_options
from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
    forced_packs_of,
    packs_choice,
    packs_of_name,
)
from chaoscypher_core.services.sources.web import (
    DiscoveryBudget,
    SiteManifest,
    SiteProgress,
    discover_site,
    pages_found,
    progress_by_site,
)


if TYPE_CHECKING:
    from rich.console import Console

    from chaoscypher_cli.context import CLIContext
    from chaoscypher_cli.sources.service import CLISourceProcessingService
    from chaoscypher_core.ports.storage_sites import SourceSiteRecord


@dataclass(frozen=True)
class SitePageOptions:
    """The ``source add`` choices every page of a site is processed with."""

    skip_index: bool = False
    skip_extract: bool = False
    skip_commit: bool = False
    skip_embeddings: bool = False
    index_only: bool = False
    extract_only: bool = False
    extraction_depth: str = "full"
    domain: str = "auto"
    filtering_mode: str | None = None
    enable_normalization: bool | None = None
    enable_vision: bool = True
    content_filtering: bool = True
    skip_duplicates: bool = False

    @classmethod
    def stored(cls, options: Mapping[str, Any] | None) -> SitePageOptions:
        """The choices a site was imported with, read back from its stored upload options.

        ``source site refresh`` runs the new and changed pages with them; a
        flag passed to the refresh overrides its one choice for that run.
        """
        stored = dict(options or {})
        forced = forced_packs_of(stored)
        return cls(
            skip_extract=not stored.get("auto_analyze", True),
            extraction_depth=str(stored.get("extraction_depth") or "full"),
            domain=packs_choice(forced) or "auto",
            filtering_mode=stored.get("filtering_mode"),
            enable_normalization=stored.get("enable_normalization"),
            enable_vision=bool(stored.get("enable_vision", True)),
            content_filtering=bool(stored.get("content_filtering", True)),
        )

    def upload_options(self) -> dict[str, Any]:
        """The pages' upload options, through the one URL-import mapping.

        A site's pages are never parked one by one for domain confirmation:
        each extracts with the packs detected for it (or ``--pack``'s); and
        ``--skip-duplicates`` does not apply to them (Core's site rules,
        ``site_page_options``).
        """
        return url_upload_options(
            extract_entities=not (self.skip_extract or self.index_only),
            analysis_depth=self.extraction_depth,
            enable_normalization=self.enable_normalization,
            enable_vision=self.enable_vision,
            forced_packs=packs_of_name(self.domain),
            skip_duplicates=self.skip_duplicates,
            content_filtering=self.content_filtering,
            filtering_mode=self.filtering_mode,
            auto_confirm=True,
        )

    def pipeline_kwargs(self) -> dict[str, Any]:
        """The ``SourcePipeline.run`` keywords for one staged page."""
        return {
            "skip_index": self.skip_index,
            "skip_extract": self.skip_extract,
            "skip_commit": self.skip_commit,
            "skip_embeddings": self.skip_embeddings,
            "index_only": self.index_only,
            "extract_only": self.extract_only,
            "extraction_depth": self.extraction_depth,
            "domain": self.domain,
            "filtering_mode": self.filtering_mode,
            "enable_normalization": self.enable_normalization,
            "enable_vision": self.enable_vision,
            "content_filtering": self.content_filtering,
            "no_confirm": True,
        }


def _upload_settings(ctx: CLIContext) -> Any:
    """The app settings the upload path stages under: this context's data dir and database."""
    from chaoscypher_core.app_config import get_settings

    settings = get_settings()
    paths = settings.paths.model_copy(update={"data_dir": str(ctx.data_dir)})
    return settings.model_copy(update={"current_database": ctx.database_name, "paths": paths})


def site_progress(ctx: CLIContext, site_id: str) -> SiteProgress:
    """A site's pages counted by status (what "43 of 71 pages" reads)."""
    counts = ctx.storage_adapter.count_site_pages([site_id], ctx.database_name)
    return progress_by_site(counts).get(site_id) or SiteProgress.empty()


def progress_line(site: SourceSiteRecord, progress: SiteProgress) -> str:
    """The progress in words: "Example Docs: 43 of 71 pages done (2 failed, 1 gone)"."""
    notes = [
        f"{progress.failed} failed" if progress.failed else "",
        f"{progress.awaiting_confirmation} awaiting confirmation"
        if progress.awaiting_confirmation
        else "",
        f"{progress.gone} gone from the site" if progress.gone else "",
    ]
    extra = ", ".join(n for n in notes if n)
    line = f"{site['title']}: {progress.done} of {progress.total} pages done"
    return f"{line} ({extra})" if extra else line


def _pages(count: int) -> str:
    """Say "1 page" or "2 pages"."""
    return "1 page" if count == 1 else f"{count} pages"


def _named(urls: list[str]) -> str:
    """A few of *urls*, then "and N more" (``web.site_pages_named_max``)."""
    from chaoscypher_core.app_config import get_settings

    return name_pages(urls, limit=get_settings().web.site_pages_named_max)


def left_out_line(site: SourceSiteRecord) -> str:
    """The pages the site leaves out: "2 pages left out: URL, URL" (or that none are)."""
    excluded = site["excluded_pages"]
    if not excluded:
        return "No pages left out."
    return f"{_pages(len(excluded))} left out: {_named(excluded)}"


def brought_back_line(back: list[str]) -> str:
    """The pages a refresh's shorter list brings back ("" when none)."""
    if not back:
        return ""
    return (
        f"Bringing back {_pages(len(back))}: {_named(back)}. "
        "Pages you deleted from the site are imported again."
    )


def preview(service: CLISourceProcessingService, url: str) -> SiteManifest:
    """Find the site's pages quickly (the preview's budget), importing nothing.

    Raises:
        ValidationError: The address is not http(s) or the SSRF check refuses it.
    """
    settings = service.ctx.settings
    manifest: SiteManifest = service.run_async(
        discover_site(url, settings, budget=DiscoveryBudget.for_preview(settings.web))
    )
    return manifest


def add_site(
    service: CLISourceProcessingService,
    console: Console,
    url: str,
    options: SitePageOptions,
    *,
    confirm: Any,
) -> int:
    """``source add URL --site``: discover, say what was found, confirm, import every page.

    Args:
        service: The CLI processing service (its loop runs the async steps).
        console: Where to print.
        url: The site address.
        options: The page choices from ``source add``'s flags.
        confirm: ``confirm(question) -> bool``; returns True to go ahead
            (the command passes ``click.confirm``, or a yes under ``--yes``).

    Returns:
        The exit code: 0 when every page was processed, 1 otherwise.
    """
    ctx = service.ctx
    existing = find_site_by_scope(
        ctx.storage_adapter,
        ctx.database_name,
        url,
        page_size=ctx.settings.pagination.default_page_size,
    )
    if existing is not None:
        console.print(
            f"[yellow]{existing['title']} ({existing['url']}) is already imported.[/yellow]"
        )
        # One line, never wrapped: the command is meant to be copied.
        console.print(
            f"Check it for updates: chaoscypher source site refresh {existing['id']}",
            soft_wrap=True,
        )
        return 1
    try:
        manifest = preview(service, url)
    except ValidationError as exc:
        console.print(f"[red]Error:[/red] {exc.message}")
        return 1
    if not manifest.pages:
        console.print(f"[yellow]No pages found at {manifest.url}.[/yellow]")
        console.print(f"To import just this page: chaoscypher source add {url}")
        return 1
    console.print(f"[bold]{manifest.title}[/bold] ({manifest.url}): {pages_found(manifest)}.")
    if not confirm("Import every page as one site?"):
        console.print("[dim]Cancelled.[/dim]")
        return 0
    site = create_site_row(
        ctx.storage_adapter,
        url=manifest.url,
        database_name=ctx.database_name,
        options=options.upload_options(),
    )
    return run_site(service, console, site, options, refresh=False)


def run_site(
    service: CLISourceProcessingService,
    console: Console,
    site: SourceSiteRecord,
    options: SitePageOptions,
    *,
    refresh: bool,
) -> int:
    """Fetch the site's pages (in this process), then run each new or changed page's pipeline.

    Returns:
        The exit code: 0 when the run and every page succeeded, 1 otherwise.
    """
    from chaoscypher_cli.sources.pipeline import SourcePipeline

    ctx = service.ctx
    console.print("[dim]Fetching pages…[/dim]")
    result = service.run_async(
        run_site_import(
            ctx.storage_adapter,
            site_id=site["id"],
            database_name=ctx.database_name,
            refresh=refresh,
            search_repository=ctx.search_repository,
            engine_settings=ctx.settings,
            settings=_upload_settings(ctx),
        )
    )
    if result.get("status") != "ok":
        console.print(f"[red]Failed:[/red] {result.get('error')}")
        return 1
    console.print(_fetch_summary(result, refresh=refresh))

    pending = pending_site_pages(ctx.storage_adapter, site["id"], ctx.database_name)
    pipeline = SourcePipeline(service, console)
    failed = 0
    for number, page in enumerate(pending, 1):
        outcome = pipeline.run(file_id=page["id"], quiet=True, **options.pipeline_kwargs())
        label = page["origin_url"] or page["title"] or page["id"]
        if outcome.parked_for_confirmation or outcome.status == SourceStatus.AWAITING_CONFIRMATION:
            mark = "[yellow]AWAITING[/yellow]"
        elif outcome.success:
            mark = "[green]OK[/green]"
        else:
            failed += 1
            mark = f"[red]FAILED[/red] {outcome.error}"
        console.print(f"[{number}/{len(pending)}] {mark} {label}")

    updated = ctx.storage_adapter.get_site(site["id"], ctx.database_name) or site
    console.print(progress_line(updated, site_progress(ctx, site["id"])))
    return 1 if failed else 0


def _fetch_summary(result: dict[str, Any], *, refresh: bool) -> str:
    """One line on what the fetch did: "Fetched 71 pages: 69 new, 2 failed"."""
    parts = [
        f"{result['added']} new" if result.get("added") else "",
        f"{result['updated']} changed" if result.get("updated") else "",
        f"{result['unchanged']} unchanged" if result.get("unchanged") else "",
        f"{result['failed']} failed" if result.get("failed") else "",
        f"{result['unreachable']} unreachable (kept)" if result.get("unreachable") else "",
        f"{result['busy']} still processing" if result.get("busy") else "",
        f"{result['gone']} gone from the site" if result.get("gone") else "",
    ]
    verb = "Checked" if refresh else "Fetched"
    detail = ", ".join(p for p in parts if p) or "nothing to do"
    return f"{verb} {result.get('pages', 0)} pages: {detail}."


def resolve_site(ctx: CLIContext, ref: str) -> SourceSiteRecord | None:
    """The site a command names: its id, address or title."""
    return find_site(
        ctx.storage_adapter,
        ctx.database_name,
        ref,
        page_size=ctx.settings.pagination.default_page_size,
    )


def refresh_blocked(site: SourceSiteRecord) -> bool:
    """Whether a live run of the site is queued or under way, so a refresh or delete must wait.

    Core's one rule (``site_run_live``): a run whose process was killed goes
    stale after the operations timeout and no longer blocks.
    """
    return site_run_live(site)


__all__ = [
    "SitePageOptions",
    "add_site",
    "brought_back_line",
    "left_out_line",
    "preview",
    "progress_line",
    "refresh_blocked",
    "resolve_site",
    "run_site",
    "site_progress",
]
