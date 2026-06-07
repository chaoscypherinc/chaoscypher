# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Commands - Chaos Cypher CLI.

The Rock Solid command structure organized by user intent:

PACKAGE MANAGEMENT (top-level, like git/docker):
- login, logout: Authentication with Lexicon Hub
- pull, push: Package transfer
- search: Hub discovery
- list, info: Package information

RUNTIME (like docker run):
- serve: Start local API/UI server
- compose: Multi-package orchestration (build, up)

BUILDER (like kubectl):
- init: Create new project structure
- node: Manipulate nodes (create, update, delete, get)
- link: Manage links between nodes (create, delete)
- template: Manage knowledge templates (create, list)
- find: Search the knowledge graph (keyword, semantic, hybrid)
- import: Ingest files into the graph

AUTOMATION:
- workflow: Execute and manage workflows (run, list)

Each command module uses Click decorators and delegates to services.
Command groups are registered in __main__.py.
"""

# Command groups are registered in __main__.py
# Individual commands are imported directly from their modules

__all__: list[str] = []
