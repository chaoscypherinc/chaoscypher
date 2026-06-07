# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tools Feature.

Tool registry and execution system for workflow agents with plugin architecture.

Components:
- ToolService: High-level tool management service (via ToolStorageProtocol)
- ToolRegistry: Plugin registry for auto-discovery
- ToolExecutionContext: Execution context for plugins

Note: ToolService depends on ToolStorageProtocol. Use the shared factory:
    from chaoscypher_core.factories import get_tool_service
    tool_service = get_tool_service(database_name)

Example:
    from chaoscypher_cortex.features.tools import ToolService, ToolRegistry

    # Use plugin registry
    registry = ToolRegistry()
    registry.discover_plugins()
    plugin = registry.get_plugin("ai.prompt")

"""

from chaoscypher_core.services.workflows.tools.management import (
    ToolExecutionContext,
    ToolRegistry,
    ToolService,
)


__all__ = [
    "ToolExecutionContext",
    # Plugin system
    "ToolRegistry",
    # Tool service
    "ToolService",
]
