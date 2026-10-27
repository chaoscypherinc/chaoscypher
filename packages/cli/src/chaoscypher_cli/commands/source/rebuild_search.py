# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Rebuild Search command - Rebuild search indexes with auto-detection.

Auto-detects whether the embedding model or dimensions have changed
and regenerates embeddings if needed, then rebuilds the vector index.
``--embed-missing`` instead embeds only the chunks and nodes written while
vector search was off (templates made then are not covered).
"""

import asyncio
import sys
from typing import Any

import click
from rich.console import Console

from chaoscypher_cli.context import get_context
from chaoscypher_core.exceptions import VectorSearchOff


console = Console()


@click.command("rebuild-search")
@click.option("--database", "-d", default="default", help="Database name")
@click.option(
    "--embed-missing",
    is_flag=True,
    help=(
        "Embed only the chunks and nodes with no vector yet (written while vector search "
        "was off); templates are not covered."
    ),
)
def rebuild_search(database: str, embed_missing: bool) -> None:
    """Rebuild search indexes.

    Auto-detects whether embeddings need regeneration:

    \b
    - If model/dimensions changed: regenerates all embeddings (slower)
    - Otherwise: rebuilds indexes from stored embeddings (fast)
    - With --embed-missing: embeds only the chunks and nodes with no vector

    \b
    Example:
        chaoscypher source rebuild-search
        chaoscypher source rebuild-search --database my-project
        chaoscypher source rebuild-search --embed-missing
    """  # noqa: D301  -- Click \x08 paragraph escape, intentional non-raw docstring.
    ctx = get_context(database_name=database)

    if embed_missing:
        _embed_missing(ctx)
        return

    needs_regeneration = ctx.search_repository.needs_full_reindex

    if needs_regeneration and ctx.embedding_service is None:
        # create_index with no provider would clear the reindex flag without re-embedding
        console.print(f"[red]Error:[/red] {VectorSearchOff('Re-embedding').message}")
        sys.exit(1)

    if needs_regeneration:
        console.print(
            "[yellow]Embedding mismatch detected.[/yellow] "
            "Regenerating all embeddings with current model..."
        )

        async def _run() -> dict:
            """Wire up search/indexing services and rebuild with regeneration."""
            from chaoscypher_core.services.search.engine.index import IndexingService
            from chaoscypher_core.services.search.engine.search import SearchService

            search_service = SearchService(
                search_repository=ctx.search_repository,
                graph_repository=ctx.graph_repository,
                indexing_repository=ctx.storage_adapter,
                source_repository=ctx.storage_adapter,
                sources_repository=ctx.storage_adapter,
                settings=ctx.settings,
            )
            indexing_service = IndexingService(
                repository=ctx.storage_adapter,
                settings=ctx.settings,
                embedding_service=ctx.embedding_service,
            )
            return await search_service.rebuild_with_regeneration(
                indexing_service=indexing_service,
            )

        result = asyncio.run(_run())
    else:
        console.print("Rebuilding search indexes from stored embeddings...")

        from chaoscypher_core.services.search.engine.search import SearchService

        search_service = SearchService(
            search_repository=ctx.search_repository,
            graph_repository=ctx.graph_repository,
            indexing_repository=ctx.storage_adapter,
            source_repository=ctx.storage_adapter,
            sources_repository=ctx.storage_adapter,
            settings=ctx.settings,
        )
        result = search_service.rebuild_indexes()

    # Display results
    if result.get("success", True):
        console.print("[green]Search indexes rebuilt successfully.[/green]")
        if needs_regeneration:
            console.print(f"  Sources re-embedded: {result.get('sources_regenerated', 0)}")
        console.print(f"  Nodes indexed: {result.get('total_nodes', 0)}")
        console.print(f"  Nodes with embeddings: {result.get('nodes_with_embeddings', 0)}")
        console.print(f"  Chunks indexed: {result.get('chunks_indexed', 0)}")
    else:
        console.print(f"[red]Rebuild failed:[/red] {result.get('message', 'Unknown error')}")
        sys.exit(1)


def _embed_missing(ctx: Any) -> None:
    """``--embed-missing``: give the rows written keyword-only their vectors, and nothing else."""
    from chaoscypher_core.services.search.engine.embed_missing import embed_missing

    provider = ctx.embedding_service
    if provider is None:
        message = VectorSearchOff("rebuild-search --embed-missing").message
        console.print(f"[red]Error:[/red] {message}")
        sys.exit(1)
    if ctx.search_repository.needs_full_reindex:
        console.print(
            "[yellow]Embedding mismatch detected:[/yellow] the stored vectors come from another "
            "model. Run 'chaoscypher source rebuild-search' first; it re-embeds everything."
        )
        sys.exit(1)

    console.print("Embedding what has no vector yet...")
    counts = asyncio.run(
        embed_missing(
            storage=ctx.storage_adapter,
            graph=ctx.graph_repository,
            search=ctx.search_repository,
            provider=provider,
            settings=ctx.settings,
        )
    )
    console.print(f"  Chunks embedded: {counts.chunks}")
    console.print(f"  Nodes embedded: {counts.nodes}")
