# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow Tools System - Plugin-Based Tool Execution.

Provides a 3-tier architecture for extensible workflow tool execution:
1. **Management**: Tool CRUD via ToolStorageProtocol (ToolService)
2. **Engine**: Execution engine with discovery and handlers (ToolExecutorService, ToolRegistry)
3. **plugins**: Auto-discoverable tool plugin implementations

The tool system supports both system tools (AI, graph, search operations) and
user-defined custom tools, with automatic plugin discovery and validation.

Architecture Overview:
    - management/: Tool service (depends on ToolStorageProtocol)
    - engine/: Execution engine (ToolExecutorService, ToolRegistry, handlers, protocols)
    - plugins/: Built-in plugin implementations (*_plugin.py)

Key Components:
    - ToolService: Manage system and user tools (CRUD via ToolStorageProtocol)
    - ToolRegistry: Auto-discover and register tool plugins
    - ToolExecutionContext: Service dependencies passed to plugin execution
    - ToolExecutorService: Execute tools via handler strategy pattern
    - BaseToolPlugin: Protocol for implementing new tools

Example:
    from chaoscypher_core.services.workflows.tools import (
        ToolService,
        ToolRegistry,
        ToolExecutorService,
        ToolExecutionContext,
        execute_tool,
    )
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

    # Manage tools via storage protocol
    adapter = SqliteAdapter(db_path="app.db")
    tool_service = ToolService(storage=adapter, database_name="default")
    tools = tool_service.list_system_tools(category="ai")

    # Plugin discovery and execution
    discovery = ToolRegistry()
    discovery.discover_plugins()
    plugin = discovery.get_plugin("ai.prompt")

    context = ToolExecutionContext(graph_manager=repo, llm_service=svc)
    result = await plugin.execute(inputs, context)

    # High-level execution
    executor = ToolExecutorService(graph_repo, search_repo, llm_svc)
    result = await executor.execute_tool("ai.prompt", inputs)

    # Convenience wrapper
    result = await execute_tool(
        tool_id="ai.prompt",
        inputs={"prompt": "Analyze..."},
        graph_manager=repo,
        llm_service=svc
    )

"""

# Engine: Execution and discovery
from chaoscypher_core.services.workflows.tools.engine import (
    AnalyticsToolHandlers,
    # Protocols
    BaseToolPlugin,
    EdgeToolHandlers,
    ExternalToolHandlers,
    # Handlers
    NodeToolHandlers,
    TemplateToolHandlers,
    ToolExecutionContext,
    # Executor
    ToolExecutorService,
    # Discovery
    ToolRegistry,
    # Validation
    ValidationResult,
    execute_tool,
    get_optional_fields,
    get_required_fields,
    get_tool_discovery,
    validate_inputs,
)

# Management: Tool service (uses ToolStorageProtocol)
from chaoscypher_core.services.workflows.tools.management import (
    ToolService,
)


__all__ = [
    "AnalyticsToolHandlers",
    # Engine: Protocols
    "BaseToolPlugin",
    "EdgeToolHandlers",
    "ExternalToolHandlers",
    # Engine: Handlers
    "NodeToolHandlers",
    "TemplateToolHandlers",
    "ToolExecutionContext",
    # Engine: Executor
    "ToolExecutorService",
    # Engine: Discovery
    "ToolRegistry",
    # Management
    "ToolService",
    # Engine: Validation
    "ValidationResult",
    "execute_tool",
    "get_optional_fields",
    "get_required_fields",
    "get_tool_discovery",
    "validate_inputs",
]
