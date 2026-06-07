# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Doctor command - Comprehensive system diagnostics.

Superset of ``chaoscypher health``: same Ollama / model / search / DB
probes, plus Lexicon hub reachability, local Cortex API reachability,
settings.yaml presence + parse status, and stale-file detection.

``health`` stays as the fast, scripted-friendly subset. ``doctor``
is the full pre-launch sweep an operator runs when something is
"off" but they don't yet know what.

Example:
    chaoscypher doctor
"""

from __future__ import annotations

import urllib.error
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import click
from rich.console import Console

from chaoscypher_cli import engine_config
from chaoscypher_cli.commands.health import (
    FAIL,
    OK,
    WARN,
    check_ollama,
    connect_context_stats,
)
from chaoscypher_cli.utils.paths import get_config_dir
from chaoscypher_core.app_config import get_settings, reload_settings


console = Console()


def check_lexicon_hub(base_url: str, timeout: float) -> tuple[bool, str | None]:
    """Probe the Lexicon hub root.

    Returns:
        (reachable, short detail message). Detail is ``None`` on
        success and a short human string on failure.
    """
    try:
        req = urllib.request.Request(base_url, method="HEAD")  # noqa: S310
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            # 2xx/3xx all count as reachable — the hub frontend may
            # 301 the root path. We're not validating the hub's API
            # shape, only that something HTTP is responding.
            if 200 <= resp.status < 400:
                return True, None
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        # 4xx/5xx still proves the host is reachable; treat as a warn,
        # not a hard fail. (E.g., a hub deploy returning 404 on HEAD /
        # is still a connectable hub.)
        return True, f"HTTP {exc.code}"
    except Exception as exc:
        return False, type(exc).__name__


def check_cortex(candidate_urls: list[str], timeout: float) -> tuple[bool, str | None]:
    """Probe candidate local Cortex API endpoints.

    The CLI works standalone without Cortex, so failure is informational.

    Args:
        candidate_urls: Base URLs to try (e.g. ``http://127.0.0.1:8000``).
        timeout: Per-attempt timeout in seconds.

    Returns:
        (reachable, base_url_that_responded). ``base_url`` is ``None`` if
        no candidate responded.
    """
    for base in candidate_urls:
        url = f"{base.rstrip('/')}/api/v1/health"
        try:
            req = urllib.request.Request(url, method="GET")  # noqa: S310
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                if 200 <= resp.status < 500:
                    return True, base
        except Exception:  # noqa: S112 — probing candidates; the next URL is the recovery
            continue
    return False, None


def check_config_file() -> tuple[bool, str, str | None]:
    """Check the unified settings.yaml file.

    Engine + client config all live in ``<data_dir>/settings.yaml`` as of the
    2026-06 config unification (cli.yaml was retired).

    Returns:
        (exists, path_as_str, parse_error_or_none). ``parse_error`` is
        ``None`` when the file is absent (which is not an error — the
        CLI ships sensible defaults) or when it parsed cleanly.
    """
    path = engine_config.settings_yaml_path()
    if not path.exists():
        return False, str(path), None

    # Re-load engine settings to surface any parse/validation error.
    # ``get_settings()`` itself falls back to defaults on YAML errors; we want
    # a structured signal here.
    try:
        reload_settings()
    except Exception as exc:
        return True, str(path), str(exc)
    return True, str(path), None


def check_stale_config_files() -> list[str]:
    """Report leftover, no-longer-read files in the config directory.

    The 2026-06 config unification retired ``cli.yaml`` (settings moved to
    settings.yaml) and ``credentials.json`` (lexicon login state moved to
    auth.json). A pure existence check — deliberately no imports of the
    lexicon subsystem — so an old file is flagged as safe to delete.

    Returns:
        A list of human-readable notices, one per stale file found.
    """
    try:
        config_dir = get_config_dir()
    except Exception:
        return []
    return [
        f"{config_dir / stale} (stale, ignored — safe to delete)"
        for stale in ("cli.yaml", "credentials.json")
        if (config_dir / stale).exists()
    ]


@click.command()
def doctor() -> None:
    """Comprehensive system diagnostics for Chaos Cypher.

    Runs every probe ``health`` runs, plus Lexicon hub reachability,
    local Cortex API reachability, and CLI config file presence.

    Use ``chaoscypher health`` for the faster scripted-friendly subset.

    Example:
        chaoscypher doctor
    """
    settings = get_settings()
    issues = 0

    console.print()
    console.print("  [bold]Chaos Cypher System Diagnostics[/bold]")
    console.print("  " + "-" * 35)

    # Engine config (LLM, embedding, lexicon) reads from settings.yaml as of
    # the 2026-06 config unification; cli.yaml no longer carries these.
    base_url = settings.llm.primary_ollama_url
    chat_model = settings.llm.ollama_chat_model
    extraction_model = settings.llm.ollama_extraction_model or settings.llm.ollama_chat_model
    embedding_model_name = settings.embedding.model
    lexicon_url = settings.lexicon.url

    # Cortex candidates: Docker dev maps the API on :8080, the all-in-one
    # image and `chaoscypher serve` default to :8000. Either is a valid
    # "local Cortex" — we just want to know if anything is listening.
    cortex_candidates = ["http://127.0.0.1:8000", "http://127.0.0.1:8080"]
    cortex_timeout = settings.cli.ollama_connect_timeout_seconds

    pool = ThreadPoolExecutor(max_workers=settings.cli.health_check_workers)
    ollama_future: Future[tuple[bool, str | None, list[str]]] = pool.submit(check_ollama, base_url)
    db_future: Future[dict[str, Any]] = pool.submit(connect_context_stats)
    hub_future: Future[tuple[bool, str | None]] = pool.submit(
        check_lexicon_hub, lexicon_url, cortex_timeout
    )
    cortex_future: Future[tuple[bool, str | None]] = pool.submit(
        check_cortex, cortex_candidates, cortex_timeout
    )

    reachable, _version, installed = ollama_future.result()

    # 1. Ollama
    if reachable:
        console.print(f"  {OK} Ollama          Connected at {base_url}")
    else:
        console.print(f"  {FAIL} Ollama          Not reachable ({base_url})")
        issues += 1

    # 2. Chat model
    if reachable and chat_model in installed:
        console.print(f"  {OK} Chat Model      {chat_model}")
    elif reachable:
        console.print(f"  {FAIL} Chat Model      {chat_model} NOT INSTALLED")
        issues += 1
    else:
        console.print(f"  {FAIL} Chat Model      {chat_model} (Ollama unreachable)")
        issues += 1

    # 3. Extraction model
    if extraction_model:
        if reachable and extraction_model in installed:
            console.print(f"  {OK} Extraction      {extraction_model}")
        elif reachable:
            console.print(f"  {FAIL} Extraction      {extraction_model} NOT INSTALLED")
            issues += 1
        else:
            console.print(f"  {FAIL} Extraction      {extraction_model} (Ollama unreachable)")
            issues += 1
    else:
        console.print(f"  {WARN} Extraction      Not configured (using chat model)")

    # 4. Embeddings
    short_name = (
        embedding_model_name.rsplit("/", maxsplit=1)[-1]
        if "/" in embedding_model_name
        else embedding_model_name
    )
    console.print(f"  {OK} Embeddings      {short_name} configured")

    # 5-6. Search index + database
    db_result = db_future.result()

    if db_result.get("context_error"):
        console.print(f"  {WARN} Search Index    Skipped (no database configured)")
        console.print(f"  {WARN} Database        Skipped (no database configured)")
    else:
        if "search_error" in db_result:
            console.print(f"  {FAIL} Search Index    Check failed")
            issues += 1
        elif "search_stats" in db_result:
            stats = db_result["search_stats"]
            fulltext = stats.get("fulltext", {}).get("document_count", 0)
            vectors = stats.get("vector", {}).get("vector_count", 0)
            if fulltext == 0 and vectors == 0:
                console.print(f"  {WARN} Search Index    Empty (no indexed content)")
            else:
                console.print(f"  {OK} Search Index    {fulltext:,} docs / {vectors:,} vectors")
        else:
            console.print(f"  {WARN} Search Index    Not available")

        if "db_error" in db_result:
            console.print(f"  {FAIL} Database        Check failed")
            issues += 1
        elif "node_count" in db_result:
            nodes = db_result["node_count"]
            edges = db_result["edge_count"]
            if nodes == 0:
                console.print(f"  {WARN} Database        Empty (0 entities)")
            else:
                console.print(
                    f"  {OK} Database        {nodes:,} entities / {edges:,} relationships"
                )
        else:
            console.print(f"  {WARN} Database        Not available")

    # 7. Lexicon hub
    hub_reachable, hub_detail = hub_future.result()
    if hub_reachable:
        suffix = f" ({hub_detail})" if hub_detail else ""
        console.print(f"  {OK} Lexicon Hub     {lexicon_url}{suffix}")
    else:
        # The CLI works without the hub; treat as a warning rather than
        # an outright failure so `chaoscypher doctor` doesn't go red on
        # an air-gapped box.
        console.print(
            f"  {WARN} Lexicon Hub     Not reachable ({lexicon_url}) — pulls/pushes will fail"
        )

    # 8. Cortex (local)
    cortex_reachable, cortex_base = cortex_future.result()
    if cortex_reachable:
        console.print(f"  {OK} Cortex API      Running at {cortex_base}")
    else:
        # Standalone CLI users don't need Cortex; informational only.
        console.print(
            f"  {WARN} Cortex API      Not running locally — start with `chaoscypher serve`"
        )

    # 9. Config file
    cfg_exists, cfg_path, cfg_error = check_config_file()
    if cfg_error is not None:
        console.print(f"  {FAIL} Config File     {cfg_path}: {cfg_error}")
        issues += 1
    elif cfg_exists:
        console.print(f"  {OK} Config File     {cfg_path}")
    else:
        console.print(f"  {WARN} Config File     {cfg_path} (not created — running with defaults)")

    # 9b. Stale, no-longer-read files left behind by the config unification.
    for notice in check_stale_config_files():
        console.print(f"  {WARN} Stale File      {notice}")

    pool.shutdown(wait=False)

    console.print()
    if issues == 0:
        console.print("  [green]All systems healthy.[/green]")
    else:
        console.print(f"  [red]{issues} issue{'s' if issues != 1 else ''} found.[/red]")
    console.print()
