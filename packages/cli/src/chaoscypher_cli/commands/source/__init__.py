# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Commands - ChaosCypher CLI.

Commands for managing sources (documents) in the knowledge graph:
- add: Import a file or URL through the processing pipeline
- list: List all sources
- get: Get detailed information about a source
- delete: Remove a source
- search: Search across sources (RAG)
- quality: Evaluate extraction quality
- extract: Trigger entity extraction (standalone, supports --force for re-extraction)

The processing pipeline handles:
1. Upload/staging (or URL fetch + staging)
2. Indexing (chunking + embeddings)
3. Entity extraction (optional, requires LLM)
4. Commit to knowledge graph

By default, `add` runs the full pipeline with progress UI. Use flags to control
which stages to run:

    --index-only      Stop after indexing
    --extract-only    Stop after extraction (skip commit)
    --skip-extract    Skip LLM extraction
    --skip-commit     Skip commit to graph

Extraction options:
    --domain DOMAIN   Domain for extraction (auto, technical, scientific, etc.)
    --quick           Fast extraction (~30s) instead of the full pass
    (--depth quick|full belongs to `source extract` / `source confirm`)

Available domains: auto (default), generic, technical, scientific, medical,
legal, financial, news, educational, biographical, historical, literary,
philosophical, political, theological

Resuming:
    chaoscypher source add --resume            # Interactive picker
    chaoscypher source add if_abc123           # By file ID
    chaoscypher source list --pending          # Show resumable files

Example:
    chaoscypher source add document.pdf                       # Full pipeline
    chaoscypher source add document.pdf --domain technical    # Force domain
    chaoscypher source add document.pdf --skip-extract        # No LLM required
    chaoscypher source add --resume                           # Resume pending file
    chaoscypher source add https://example.com/article        # Import from URL
    chaoscypher source add https://example.com --quick        # URL with fast extraction
    chaoscypher source list --pending                         # Show resumable files
    chaoscypher source get if_abc123
    chaoscypher source delete if_abc123
    chaoscypher source search "machine learning"
    chaoscypher source quality score <source_id>
"""

import click

from chaoscypher_cli.lazy import LazyGroup


LAZY_SUBCOMMANDS = {
    "add": (
        "chaoscypher_cli.commands.source.add:add",
        "Add files, dirs, or URLs (--quick, --json, -v, -q)",
    ),
    "extract": (
        "chaoscypher_cli.commands.source.extract:extract_cmd",
        "Run entity extraction on an indexed source (--force to re-extract committed)",
    ),
    "confirm": (
        "chaoscypher_cli.commands.source.confirm:confirm_cmd",
        "Confirm a parked source's domain and extract (--all, --domain, -y)",
    ),
    "list": (
        "chaoscypher_cli.commands.source.list:list_files",
        "List all sources (--pending, --format json)",
    ),
    "get": ("chaoscypher_cli.commands.source.get:get", "Get source details"),
    "delete": ("chaoscypher_cli.commands.source.delete:delete", "Delete a source"),
    "search": (
        "chaoscypher_cli.commands.source.search:search",
        "Search across sources (--format json)",
    ),
    "rebuild-search": (
        "chaoscypher_cli.commands.source.rebuild_search:rebuild_search",
        "Rebuild search indexes (auto-detects model changes)",
    ),
    "quality": ("chaoscypher_cli.commands.quality:quality", "Evaluate extraction quality"),
}


@click.group(cls=LazyGroup, lazy_subcommands=LAZY_SUBCOMMANDS)
def source() -> None:
    """Manage sources (documents) in the knowledge graph.

    \b
    Quick reference for 'source add':
      --quick           Fast extraction (~30s)
      --skip-extract    Skip LLM extraction
      --index-only      Stop after indexing
      --verbose / -v    Show real-time logs
      --quiet / -q      Minimal output (just OK/FAILED)
      --json            Output results as JSON
      --domain DOMAIN   Force domain (technical, scientific, ...)
      --resume / -r     Interactive picker for pending files

    \b
    Quick reference for 'source extract':
      --depth DEPTH     quick or full (default: full)
      --domain DOMAIN   Force extraction domain
      --force           Re-extract a committed source (deletes graph artifacts)
      --yes / -y        Skip the --force confirmation prompt

    \b
    Quick reference for 'source confirm':
      <id> / --all      Confirm one parked source, or all of them
      --domain DOMAIN   Override the recommended domain
      --yes / -y        Accept the recommendation without prompting
    """  # noqa: D301


__all__ = ["source"]
