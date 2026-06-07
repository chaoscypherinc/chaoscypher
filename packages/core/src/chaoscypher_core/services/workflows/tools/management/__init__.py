# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool Registry Service - Business logic for tool management.

This module provides framework-agnostic tool management that works in both:
- CLI environments (synchronous)
- Docker/FastAPI environments (asynchronous)

This module provides:
- ToolService: Manage system and user tools (via ToolStorageProtocol)
- Plugin System: Auto-discovery and execution of tool plugins

The ToolService depends on ToolStorageProtocol, which is implemented by:
- SqliteAdapter + ToolsMixin (production, in adapters/sqlite/)

Usage:
    from chaoscypher_core.services.workflows.tools.management import ToolService
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

    # Create service with storage adapter
    adapter = SqliteAdapter(db_path="app.db")
    service = ToolService(storage=adapter, database_name="default")

    # List tools
    tools = service.list_system_tools(category="ai")

    # Plugin system
    from chaoscypher_core.services.workflows.tools.management import ToolRegistry
    registry = ToolRegistry()
    registry.discover_plugins()
    plugin = registry.get_plugin("ai.prompt")
"""

from chaoscypher_core.services.workflows.tools.engine import ToolExecutionContext, ToolRegistry
from chaoscypher_core.services.workflows.tools.management.service import ToolService


__all__ = [
    "ToolExecutionContext",
    # Plugin system
    "ToolRegistry",
    # Tool service
    "ToolService",
]
