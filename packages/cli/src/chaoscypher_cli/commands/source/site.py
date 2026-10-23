# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Site commands: the docs sites imported with ``source add URL --site``.

- list: every site with its progress ("43 of 71 pages done")
- show: one site and its pages (a page the site no longer lists is marked)
- refresh: check a site for updates (only changed pages are re-imported);
  the pages the site leaves out stay out (``--exclude`` replaces that list)
- delete: remove a site, keeping its pages as ordinary sources unless told

A site is named by its id, its address, or its title.

Example:
    chaoscypher source site list
    chaoscypher source site show "Ollama documentation"
    chaoscypher source site refresh https://docs.ollama.com
    chaoscypher source site refresh https://docs.ollama.com --exclude https://docs.ollama.com/faq
    chaoscypher source site delete https://docs.ollama.com --delete-pages
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console
from rich.table import Table

from chaoscypher_cli.context import get_context
from chaoscypher_cli.utils.console import print_json


if TYPE_CHECKING:
    from chaoscypher_cli.context import CLIContext
    from chaoscypher_core.ports.storage_sites import SourceSiteRecord


console = Console()


def _require_site(ctx: CLIContext, ref: str) -> SourceSiteRecord:
    """The site ``ref`` names, or exit 1 saying none does."""
    from chaoscypher_cli.sources.sites import resolve_site

    site = resolve_site(ctx, ref)
    if site is None:
        console.print(f"[red]No site matches:[/red] {ref}")
        console.print("List sites with: chaoscypher source site list")
        sys.exit(1)
    return site


def _when(value: Any) -> str:
    """A timestamp as ``YYYY-MM-DD HH:MM``, or ``-``."""
    return value.strftime("%Y-%m-%d %H:%M") if value is not None else "-"


@click.group("site")
def site() -> None:
    """Imported docs sites: list, show, check for updates, delete.

    \b
    Import one with: chaoscypher source add https://docs.example.com --site
    """  # noqa: D301


@site.command("list")
@click.option(
    "--format",
    "-f",
    "output_format",
    default="table",
    type=click.Choice(["table", "json"]),
    help="Output format",
)
@click.option("--database", "-d", default="default", help="Database name")
def list_sites(output_format: str, database: str) -> None:
    """List the imported sites, newest first, with their progress."""
    from chaoscypher_cli.sources.sites import site_progress

    ctx = get_context(database_name=database)
    sites, _total = ctx.storage_adapter.list_sites(
        ctx.database_name, page=1, page_size=ctx.settings.pagination.max_page_size
    )
    rows = [(s, site_progress(ctx, s["id"])) for s in sites]
    if output_format == "json":
        payload = [{**s, "progress": p.model_dump()} for s, p in rows]
        print_json(json.dumps(payload, indent=2, default=str))
        return
    if not rows:
        console.print("[dim]No sites imported.[/dim]")
        console.print("\nImport one with: chaoscypher source add <URL> --site")
        return
    table = Table(title="Sites", show_header=True)
    # Ids and addresses fold rather than lose their tail: a cut id names nothing.
    table.add_column("ID", style="dim", overflow="fold")
    table.add_column("Title", style="cyan")
    table.add_column("Address", overflow="fold")
    table.add_column("Pages", justify="right")
    table.add_column("Status", style="green")
    table.add_column("Checked", style="dim")
    for s, progress in rows:
        pages = f"{progress.done} of {progress.total}"
        if progress.gone:
            pages += f" ({progress.gone} gone)"
        table.add_row(
            s["id"], s["title"], s["url"], pages, s["status"], _when(s["last_checked_at"])
        )
    console.print(table)


@site.command("show")
@click.argument("site_ref")
@click.option("--database", "-d", default="default", help="Database name")
def show_site(site_ref: str, database: str) -> None:
    """Show one site and its pages (SITE_REF: id, address or title)."""
    from chaoscypher_cli.sources.sites import left_out_line, progress_line, site_progress
    from chaoscypher_core.operations.sources.site_runs import site_leaves_out
    from chaoscypher_core.services.sources.web import DiscoveryMethod

    ctx = get_context(database_name=database)
    record = _require_site(ctx, site_ref)
    console.print(f"[bold]{record['title']}[/bold]  {record['url']}")
    found_by = record["discovery_method"]
    method = DiscoveryMethod(found_by).label if found_by else "-"
    console.print(
        f"[dim]ID:[/dim] {record['id']}  [dim]Status:[/dim] {record['status']}  "
        f"[dim]Found by:[/dim] {method}  [dim]Checked:[/dim] {_when(record['last_checked_at'])}"
    )
    if record["error_message"]:
        console.print(f"[red]{record['error_message']}[/red]")
    console.print(progress_line(record, site_progress(ctx, record["id"])))
    console.print(left_out_line(record), soft_wrap=True)

    pages = ctx.storage_adapter.list_site_pages(record["id"], ctx.database_name)
    if not pages:
        return
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Page", style="cyan", overflow="fold")
    table.add_column("Status")
    table.add_column("ID", style="dim", overflow="fold")
    for page in pages:
        status = page["status"]
        if page["site_gone_at"] is not None:
            status += " [yellow](gone from site)[/yellow]"
        if site_leaves_out(record, page["origin_url"]):
            status += " [dim](left out, not checked for updates)[/dim]"
        table.add_row(page["origin_url"] or page["title"] or "", status, page["id"])
    console.print()
    console.print(table)


@site.command("refresh")
@click.argument("site_ref")
@click.option(
    "--skip-extract/--extract",
    default=None,
    help="Index the new and changed pages without extraction, or extract them "
    "(default: as the site was imported)",
)
@click.option("--skip-embeddings", is_flag=True, help="Skip embedding generation during indexing")
@click.option(
    "--quick/--full",
    default=None,
    help="Quick or full extraction for the new and changed pages "
    "(default: as the site was imported)",
)
@click.option(
    "--exclude",
    "exclude",
    multiple=True,
    metavar="URL",
    help="Leave this page out from now on (repeatable); replaces the pages the "
    "site leaves out (default: keep them out as before)",
)
@click.option(
    "--include-all",
    is_flag=True,
    help="Bring back every page the site leaves out (an empty --exclude list)",
)
@click.option("--database", "-d", default="default", help="Database name")
def refresh_site(
    site_ref: str,
    skip_extract: bool | None,
    skip_embeddings: bool,
    quick: bool | None,
    exclude: tuple[str, ...],
    include_all: bool,
    database: str,
) -> None:
    """Check a site for updates (SITE_REF: id, address or title).

    Every page is fetched again: an unchanged page is left alone, a changed
    one is re-imported in place, a new one is added, and one the site no
    longer lists is marked gone (never deleted). New and changed pages run
    with the choices the site was imported with; a flag passed here
    overrides that one choice for this check.

    The pages the site leaves out (unticked at import, passed to --exclude
    before, or deleted from the site) stay out. --exclude replaces that list
    from now on; --include-all brings every page back.
    """
    import dataclasses

    from chaoscypher_cli.sources import CLISourceProcessingService
    from chaoscypher_cli.sources.sites import (
        SitePageOptions,
        brought_back_line,
        left_out_line,
        refresh_blocked,
        run_site,
    )
    from chaoscypher_core.exceptions import ValidationError
    from chaoscypher_core.operations.sources.site_runs import begin_site_refresh

    if include_all and exclude:
        raise click.UsageError("--include-all and --exclude cannot be used together.")
    ctx = get_context(database_name=database)
    record = _require_site(ctx, site_ref)
    if refresh_blocked(record):
        console.print(
            f"[yellow]{record['title']} is already being imported or checked "
            f"(status: {record['status']}).[/yellow]"
        )
        sys.exit(1)
    wanted = [] if include_all else (list(exclude) if exclude else None)
    try:
        back = begin_site_refresh(
            ctx.storage_adapter, record["id"], ctx.database_name, exclude=wanted
        )
    except ValidationError as exc:
        console.print(f"[red]Error:[/red] {exc.message}")
        sys.exit(1)
    record = ctx.storage_adapter.get_site(record["id"], ctx.database_name) or record
    overrides: dict[str, Any] = {"skip_embeddings": skip_embeddings}
    if skip_extract is not None:
        overrides["skip_extract"] = skip_extract
    if quick is not None:
        overrides["extraction_depth"] = "quick" if quick else "full"
    options = dataclasses.replace(SitePageOptions.stored(record["options"]), **overrides)
    console.print(f"Checking [bold]{record['title']}[/bold] for updates.")
    if back:
        console.print(brought_back_line(back), soft_wrap=True)
    if record["excluded_pages"]:
        console.print(left_out_line(record), soft_wrap=True)
    with CLISourceProcessingService(ctx) as service:
        code = run_site(service, console, record, options, refresh=True)
    if code:
        sys.exit(code)


@site.command("delete")
@click.argument("site_ref")
@click.option(
    "--delete-pages",
    is_flag=True,
    help="Delete the site's pages too (default: keep them as ordinary sources).",
)
@click.option("--yes", "yes", is_flag=True, help="Delete without asking")
@click.option("--database", "-d", default="default", help="Database name")
def delete_site(site_ref: str, delete_pages: bool, yes: bool, database: str) -> None:
    """Delete a site (SITE_REF: id, address or title); its pages stay unless --delete-pages.

    Refused while a run of the site is queued or under way.
    """
    from chaoscypher_cli.sources.deletion import delete_source_cascade
    from chaoscypher_cli.sources.sites import refresh_blocked

    ctx = get_context(database_name=database)
    record = _require_site(ctx, site_ref)
    if refresh_blocked(record):
        console.print(
            f"[yellow]{record['title']} is being imported or checked "
            f"(status: {record['status']}); delete it once that run ends.[/yellow]"
        )
        sys.exit(1)
    pages = ctx.storage_adapter.list_site_pages(record["id"], ctx.database_name)
    what = (
        f"{record['title']} and its {len(pages)} pages"
        if delete_pages
        else f"{record['title']} (its {len(pages)} pages stay as sources)"
    )
    if not yes and not click.confirm(f"Delete {what}?", default=False):
        console.print("[dim]Cancelled.[/dim]")
        return
    if delete_pages:
        for page in pages:
            # The site row goes next: its pages are not written to its list.
            delete_source_cascade(ctx, page["id"], leave_out_of_site=False)
    ctx.storage_adapter.delete_site(record["id"], ctx.database_name)
    console.print(f"[green]Deleted[/green] {what}.")


__all__ = ["site"]
