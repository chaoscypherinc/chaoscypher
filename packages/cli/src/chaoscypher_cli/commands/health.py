# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Health command - Check system health status.

Checks Ollama connectivity, model availability, embeddings,
search index, and database status.

Example:
    chaoscypher health
"""

import json
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import click
from rich.console import Console

from chaoscypher_core.app_config import get_settings


console = Console()

# ASCII-safe status indicators (Unicode ✓/✗ fail on Windows cp1252 consoles).
# Re-exported here (and from `doctor.py`) so other diagnostic commands can
# render with the same vocabulary.
OK = "[green]+[/green]"
FAIL = "[red]x[/red]"
WARN = "[yellow]![/yellow]"

# Backwards-compatible aliases — referenced by tests and any out-of-tree code
# that imported the underscore-prefixed names before the doctor refactor.
_OK = OK
_FAIL = FAIL
_WARN = WARN


def check_ollama(base_url: str) -> tuple[bool, str | None, list[str]]:
    """Check Ollama connectivity and list installed models.

    Uses a single /api/tags call to prove connectivity and get models.

    Args:
        base_url: Ollama base URL.

    Returns:
        Tuple of (reachable, version, installed_model_names).
    """
    # Single call: /api/tags proves connectivity AND gives models
    try:
        req = urllib.request.Request(f"{base_url}/api/tags", method="GET")  # noqa: S310
        timeout_seconds = get_settings().cli.ollama_connect_timeout_seconds
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
            models = [m.get("name", "unknown") for m in data.get("models", [])]
    except Exception:
        return False, None, []

    return True, None, models


def connect_context_stats() -> dict[str, Any]:
    """Initialize CLIContext and gather DB/search stats in a background thread.

    Returns:
        Dict with search_stats, node_count, edge_count, or error keys.
    """
    try:
        from chaoscypher_cli.context import CLIContext

        ctx = CLIContext()
        ctx.connect()

        result: dict[str, Any] = {}

        search_repo = getattr(ctx, "search_repository", None)
        if search_repo:
            try:
                result["search_stats"] = search_repo.get_index_stats()
            except Exception:
                result["search_error"] = True

        graph_repo = getattr(ctx, "graph_repository", None)
        if graph_repo:
            try:
                result["node_count"] = graph_repo.count_nodes()
                result["edge_count"] = graph_repo.count_edges()
            except Exception:
                result["db_error"] = True

        return result

    except Exception:
        return {"context_error": True}


@click.command()
def health() -> None:
    """Check system health status.

    Checks Ollama, models, embeddings, search index, and database.

    Example:
        chaoscypher health
    """
    settings = get_settings()
    issues = 0

    console.print()
    console.print("  [bold]Chaos Cypher System Health[/bold]")
    console.print("  " + "-" * 35)

    # Read engine config from settings.yaml (the single source of truth as of
    # the 2026-06 config unification; cli.yaml no longer carries LLM settings).
    base_url = settings.llm.primary_ollama_url
    chat_model = settings.llm.ollama_chat_model
    extraction_model = settings.llm.ollama_extraction_model or settings.llm.ollama_chat_model
    embedding_model_name = settings.embedding.model

    # Kick off Ollama check and DB connect in parallel
    pool = ThreadPoolExecutor(max_workers=settings.cli.health_check_workers)
    ollama_future: Future[tuple[bool, str | None, list[str]]] = pool.submit(check_ollama, base_url)
    db_future: Future[dict[str, Any]] = pool.submit(connect_context_stats)

    # Wait for Ollama result (DB connect continues in background)
    reachable, _version, installed = ollama_future.result()

    # 1. Ollama connectivity
    if reachable:
        console.print(f"  {_OK} Ollama          Connected at {base_url}")
    else:
        console.print(f"  {_FAIL} Ollama          Not reachable ({base_url})")
        issues += 1

    # 2. Chat model
    if reachable and chat_model in installed:
        console.print(f"  {_OK} Chat Model      {chat_model}")
    elif reachable:
        console.print(f"  {_FAIL} Chat Model      {chat_model} NOT INSTALLED")
        issues += 1
    else:
        console.print(f"  {_FAIL} Chat Model      {chat_model} (Ollama unreachable)")
        issues += 1

    # 3. Extraction model
    if extraction_model:
        if reachable and extraction_model in installed:
            console.print(f"  {_OK} Extraction      {extraction_model}")
        elif reachable:
            console.print(f"  {_FAIL} Extraction      {extraction_model} NOT INSTALLED")
            issues += 1
        else:
            console.print(f"  {_FAIL} Extraction      {extraction_model} (Ollama unreachable)")
            issues += 1
    else:
        console.print(f"  {_WARN} Extraction      Not configured (using chat model)")

    # 4. Embeddings
    short_name = (
        embedding_model_name.rsplit("/", maxsplit=1)[-1]
        if "/" in embedding_model_name
        else embedding_model_name
    )
    console.print(f"  {_OK} Embeddings      {short_name} configured")

    # 5-6. Search index and database (already running in background)
    db_result = db_future.result()

    if db_result.get("context_error"):
        console.print(f"  {_WARN} Search Index    Skipped (no database configured)")
        console.print(f"  {_WARN} Database        Skipped (no database configured)")
    else:
        # Search index
        if "search_error" in db_result:
            console.print(f"  {_FAIL} Search Index    Check failed")
            issues += 1
        elif "search_stats" in db_result:
            stats = db_result["search_stats"]
            fulltext = stats.get("fulltext", {}).get("document_count", 0)
            vectors = stats.get("vector", {}).get("vector_count", 0)
            if fulltext == 0 and vectors == 0:
                console.print(f"  {_WARN} Search Index    Empty (no indexed content)")
            else:
                console.print(f"  {_OK} Search Index    {fulltext:,} docs / {vectors:,} vectors")
        else:
            console.print(f"  {_WARN} Search Index    Not available")

        # Database
        if "db_error" in db_result:
            console.print(f"  {_FAIL} Database        Check failed")
            issues += 1
        elif "node_count" in db_result:
            nodes = db_result["node_count"]
            edges = db_result["edge_count"]
            if nodes == 0:
                console.print(f"  {_WARN} Database        Empty (0 entities)")
            else:
                console.print(
                    f"  {_OK} Database        {nodes:,} entities / {edges:,} relationships"
                )
        else:
            console.print(f"  {_WARN} Database        Not available")

    pool.shutdown(wait=False)

    # Summary
    console.print()
    if issues == 0:
        console.print("  [green]All systems healthy.[/green]")
    else:
        console.print(f"  [red]{issues} issue{'s' if issues != 1 else ''} found.[/red]")
    console.print()
