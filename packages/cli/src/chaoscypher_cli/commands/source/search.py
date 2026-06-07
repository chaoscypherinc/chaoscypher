# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Search command - Search sources in the knowledge graph.

Provides keyword, semantic, and hybrid search modes for finding content
in indexed sources.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console
from rich.table import Table

from chaoscypher_cli.context import get_context
from chaoscypher_core.app_config import get_settings


if TYPE_CHECKING:
    from chaoscypher_cli.context import CLIContext

console = Console()


@click.command()
@click.argument("query")
@click.option(
    "--mode",
    "-m",
    type=click.Choice(["hybrid", "keyword", "semantic"]),
    default="hybrid",
    help="Search mode: hybrid (default), keyword (fast), semantic (AI only)",
)
@click.option(
    "--limit",
    "-l",
    type=int,
    default=lambda: get_settings().cli.search_default_limit,
    show_default="from settings.cli.search_default_limit",
    help="Max results",
)
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format",
)
@click.option("--database", "-d", default="default", help="Database name")
def search(
    query: str,
    mode: str,
    limit: int,
    output_format: str,
    database: str,
) -> None:
    r"""Search across sources in the knowledge graph.

    Supports three search modes:

    \b
    - hybrid (default): Semantic + keyword fallback - most robust
    - keyword: Fast full-text search
    - semantic: Pure vector similarity (local CPU embeddings)

    \b
    Examples:
        chaoscypher source search "machine learning"
        chaoscypher source search "API" --mode keyword --limit 20
        chaoscypher source search "neural networks" --mode semantic
    """
    try:
        ctx = get_context(database_name=database)

        console.print(f"[cyan]Searching:[/cyan] {query} [dim]({mode} mode)[/dim]")

        # Perform search
        results = _perform_search(ctx, query, mode, limit)

        if not results:
            console.print("[yellow]No results found.[/yellow]")
            return

        # Display results
        if output_format == "json":
            _display_json(results)
        else:
            _display_table(results)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def _perform_search(ctx: CLIContext, query: str, mode: str, limit: int) -> list[dict[str, Any]]:
    """Perform search using SearchRepository.

    Args:
        ctx: CLI context with repositories.
        query: Search query string.
        mode: Search mode (keyword, semantic, hybrid).
        limit: Maximum results to return.

    Returns:
        List of search result dictionaries.
    """
    search_repo = ctx.search_repository

    if mode == "keyword":
        # Keyword search - synchronous
        raw_results = search_repo.keyword_search(query, limit=limit)
    elif mode == "semantic":
        # Semantic search - needs LLM callback for query embedding
        raw_results = asyncio.run(
            search_repo.semantic_search(
                query,
                k=limit,
                embedding_provider_callback=_get_embedding_callback(ctx),
            )
        )
    else:  # hybrid
        raw_results = asyncio.run(
            search_repo.hybrid_search(
                query,
                k=limit,
                embedding_provider_callback=_get_embedding_callback(ctx),
            )
        )

    # Hydrate results with node data
    return _hydrate_results(ctx, raw_results)


def _get_embedding_callback(ctx: CLIContext) -> Any:
    """Get embedding callback for semantic search.

    Args:
        ctx: CLI context with embedding service.

    Returns:
        Async callback function that generates embeddings.
        Returns the embedding vector (list[float]) directly.
    """

    async def callback(text: str) -> list[float]:
        """Embed a query string and return the raw vector."""
        result = await ctx.embedding_service.embed(text)
        return result.embedding

    return callback


def _hydrate_results(ctx: CLIContext, raw_results: list[tuple[str, float]]) -> list[dict[str, Any]]:
    """Hydrate search results with full node data.

    Uses batch retrieval for efficiency (avoids N+1 queries).
    Handles both node IDs and chunk IDs (prefixed with "chunk:").

    Args:
        ctx: CLI context with graph repository.
        raw_results: List of (id, score) tuples (node_id or "chunk:uuid").

    Returns:
        List of result dictionaries with node or chunk data.
    """
    if not raw_results:
        return []

    # Separate node IDs from chunk IDs
    node_ids = []
    chunk_entries = []  # (chunk_uuid, score)
    for result_id, score in raw_results:
        if result_id.startswith("chunk:"):
            chunk_uuid = result_id[6:]  # Remove "chunk:" prefix
            chunk_entries.append((chunk_uuid, score))
        else:
            node_ids.append((result_id, score))

    results: list[dict[str, Any]] = []

    # Hydrate nodes
    if node_ids:
        ids_only = [nid for nid, _ in node_ids]
        nodes = ctx.graph_repository.get_nodes_batch(ids_only)
        nodes_dict = {node.id: node for node in nodes}

        for node_id, score in node_ids:
            node = nodes_dict.get(node_id)
            if node:
                results.append(
                    {
                        "id": node_id,
                        "label": node.label or "",
                        "template_id": node.template_id or "",
                        "score": round(score, 3),
                        "properties": node.properties or {},
                        "result_type": "node",
                    }
                )

    # Hydrate chunks (fetch directly from storage adapter)
    if chunk_entries:
        for chunk_uuid, score in chunk_entries:
            chunk_data = ctx.storage_adapter.get_chunk_by_id(chunk_uuid)
            if chunk_data:
                # Truncate content for display
                content = chunk_data.get("content", "")
                if len(content) > 100:
                    content = content[:100] + "..."
                results.append(
                    {
                        "id": f"chunk:{chunk_uuid}",
                        "label": content,
                        "template_id": f"chunk #{chunk_data.get('chunk_index', '?')}",
                        "score": round(score, 3),
                        "properties": {"source_id": chunk_data.get("source_id")},
                        "result_type": "chunk",
                    }
                )

    # Sort by score descending (since we combined two lists)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def _display_table(results: list[dict[str, Any]]) -> None:
    """Display results as a Rich table.

    Args:
        results: List of result dictionaries.
    """
    table = Table(title=f"Search Results ({len(results)} found)")
    table.add_column("Score", style="green", width=6)
    table.add_column("Label", style="cyan")
    table.add_column("Template", style="dim")
    table.add_column("ID", style="dim", width=20)

    for r in results:
        label = r["label"]
        if len(label) > 50:
            label = label[:50] + "..."

        node_id = r["id"]
        if len(node_id) > 20:
            node_id = node_id[:20] + "..."

        table.add_row(
            str(r["score"]),
            label,
            r["template_id"],
            node_id,
        )

    console.print(table)


def _display_json(results: list[dict[str, Any]]) -> None:
    """Display results as JSON.

    Args:
        results: List of result dictionaries.
    """
    console.print(json.dumps(results, indent=2))
