# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool Execution Engine.

Workflow tool execution engine with plugin registry, protocols, and handlers.

Components:
- Executor: ToolExecutorService class and execute_tool() convenience function
- Registry: ToolRegistry for auto-discovering plugins
- Protocols: BaseToolPlugin, ToolExecutionContext
- Validation: Input validation helpers
- Handlers: Specialized handlers for different tool categories

Example:
    from chaoscypher_core.services.workflows.tools.engine import (
        ToolExecutorService,
        ToolRegistry,
        ToolExecutionContext,
        execute_tool,
    )

    # Registry (auto-discovers plugins on init)
    registry = ToolRegistry()
    plugin = registry.get("ai.prompt")

    # Execute
    context = ToolExecutionContext(graph_manager=repo, llm_service=svc)
    result = await plugin.execute(inputs, context)

    # Or use convenience wrapper
    result = await execute_tool(
        tool_id="ai.prompt",
        inputs={"prompt": "..."},
        graph_manager=repo,
        llm_service=svc
    )

"""

# Protocols
from chaoscypher_core.services.workflows.tools.engine.base import BaseToolPlugin
from chaoscypher_core.services.workflows.tools.engine.context import ToolExecutionContext
from chaoscypher_core.services.workflows.tools.engine.executor import (
    ToolExecutorService,
    execute_tool,
    get_tool_discovery,
)

# Handlers
from chaoscypher_core.services.workflows.tools.engine.handlers import (
    AnalyticsToolHandlers,
    EdgeToolHandlers,
    ExternalToolHandlers,
    NodeToolHandlers,
    TemplateToolHandlers,
)

# Registry
from chaoscypher_core.services.workflows.tools.engine.registry import ToolRegistry

# Schema Registry
from chaoscypher_core.services.workflows.tools.engine.schema_registry import (
    TOOL_SCHEMAS,
    get_essential_tool_schemas,
    get_tool_schema,
    get_tool_schemas,
)

# Validation
from chaoscypher_core.services.workflows.tools.engine.validators import (
    ValidationResult,
    get_optional_fields,
    get_required_fields,
    validate_inputs,
)


__all__ = [
    # Schema Registry
    "TOOL_SCHEMAS",
    "AnalyticsToolHandlers",
    # Protocols
    "BaseToolPlugin",
    "EdgeToolHandlers",
    "ExternalToolHandlers",
    # Handlers
    "NodeToolHandlers",
    "TemplateToolHandlers",
    "ToolExecutionContext",
    # Executor
    "ToolExecutorService",
    # Registry
    "ToolRegistry",
    # Validation
    "ValidationResult",
    "execute_tool",
    "get_essential_tool_schemas",
    "get_optional_fields",
    "get_required_fields",
    "get_tool_discovery",
    "get_tool_schema",
    "get_tool_schemas",
    "validate_inputs",
]
