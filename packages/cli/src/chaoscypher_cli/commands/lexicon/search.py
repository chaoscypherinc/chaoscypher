# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Search command - Search Lexicon Hub for packages.

Uses the core LexiconClient to search the package registry.

Example:
    chaoscypher lexicon search "medical ontology"
    chaoscypher lexicon search "research" --tag biomedical --limit 10
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import click
from rich.table import Table

from chaoscypher_cli.commands.lexicon.login import get_auth_config, get_lexicon_url
from chaoscypher_cli.utils.console import get_console, print_error
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.services.lexicon import LexiconClient, LexiconClientError


def format_downloads(count: int) -> str:
    """Format download count for display.

    Args:
        count: Download count.

    Returns:
        Formatted string (e.g., "1.2k", "15.3k").
    """
    if count >= 1000:
        return f"{count / 1000:.1f}k"
    return str(count)


@click.command()
@click.argument("query")
@click.option(
    "--limit",
    "-n",
    type=int,
    default=lambda: get_settings().cli.search_default_limit,
    show_default="from settings.cli.search_default_limit",
    help="Maximum results to show",
)
@click.option("--tag", "-t", multiple=True, help="Filter by tags")
@click.option("--author", "-a", help="Filter by author username")
@click.option(
    "--sort",
    "-s",
    default="relevance",
    type=click.Choice(["relevance", "downloads", "updated", "name"]),
    help="Sort results by",
)
def search(query: str, limit: int, tag: tuple[str, ...], author: str | None, sort: str) -> None:
    """Search the Lexicon Hub for packages.

    QUERY is the search term to look for in package names,
    descriptions, and tags.

    Example:
        chaoscypher lexicon search "medical ontology"
        chaoscypher lexicon search "research" --tag biomedical --limit 10
        chaoscypher lexicon search "nlp" --author john --sort downloads
    """
    console = get_console()
    console.print(f"[cyan]Searching Lexicon Hub:[/cyan] {query}\n")

    # Get auth and hub URL
    auth = get_auth_config()
    lexicon_url = get_lexicon_url()

    # Build search query - incorporate tag and author filters into query string
    search_query = query
    if tag:
        search_query = f"{search_query} {' '.join(tag)}"

    async def do_search() -> list[Any]:
        """Run the package search against the Lexicon hub."""
        async with LexiconClient(base_url=lexicon_url, auth=auth) as client:
            packages, _total = await client.search(
                query=search_query,
                limit=limit,
                sort_by=sort,
            )
            return packages

    try:
        with console.status("[dim]Searching...[/dim]"):
            results = asyncio.run(do_search())

        # Filter by author locally if specified
        if author:
            results = [p for p in results if p.owner_username == author]

        if not results:
            console.print("[dim]No packages found matching your query.[/dim]")
            return

        # Display results in table
        table = Table(show_header=True, title=f"Found {len(results)} package(s)")
        table.add_column("Package", style="cyan")
        table.add_column("Version", style="dim")
        table.add_column("Owner", style="green")
        table.add_column("Description", style="white", max_width=50)
        table.add_column("Downloads", style="dim", justify="right")

        for pkg in results:
            # Truncate description if needed
            desc = pkg.description or ""
            if len(desc) > 50:
                desc = desc[:47] + "..."

            table.add_row(
                pkg.name,
                pkg.version,
                pkg.owner_username,
                desc,
                format_downloads(pkg.download_count),
            )

        console.print(table)

        # Show install hint
        if results:
            first_pkg = results[0]
            console.print(f"\n[dim]Install with:[/dim] chaoscypher pull {first_pkg.full_name}")

    except LexiconClientError as e:
        print_error(f"Search failed: {e}")
        sys.exit(1)
