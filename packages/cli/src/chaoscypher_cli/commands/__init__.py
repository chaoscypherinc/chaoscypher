# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Commands - Chaos Cypher CLI.

The command tree organized by user intent:

SETUP:
- setup: First-run wizard (LLM provider configuration)
- config: View and manage CLI configuration
- completions: Generate shell completion scripts

PACKAGE MANAGEMENT (like git/docker):
- pull, push: Package transfer (root-level aliases)
- lexicon: Hub account and packages (login, logout, whoami, search,
  list, info, remove, pull, push)

RUNTIME (like docker run):
- serve: Start local API/UI server
- compose: Multi-package orchestration (build, up)
- mcp: MCP server over stdio

BUILDER (like kubectl):
- source: Document pipeline (add, extract, confirm, list, get, delete,
  search, rebuild-search) plus quality scoring (source quality ...)
- graph: Knowledge graph CRUD — node, link, template (full CRUD),
  workflow (list, get), package (export, load)

AI:
- chat: Chat with AI using your knowledge graph

MAINTENANCE & DIAGNOSTICS:
- db: Database management (create, list, switch, delete, info, migrate)
- upgrade: Apply pending Alembic migrations
- health, doctor, diagnostics: System checks and bug-report bundles
- benchmark: Run and inspect the extraction benchmark
- render-orchestration: Render orchestration configs (dev/debug)

Each command module uses Click decorators and delegates to services.
Command groups are registered in __main__.py.
"""

# Command groups are registered in __main__.py
# Individual commands are imported directly from their modules

__all__: list[str] = []
