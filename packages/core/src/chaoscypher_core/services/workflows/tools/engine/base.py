# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Base Plugin Protocol for Workflow Tools.

Defines the interface that all tool plugins must implement. Tools are auto-discovered
from the plugins directory and registered for execution within workflows.

Architecture:
    - Protocol-based design for type safety and flexibility
    - Each plugin is a single file (e.g., prompt.py, search.py)
    - Auto-discovery via registry.py (scans *.py files)
    - No inheritance required (structural typing via Protocol)

Example Plugin:
    ```python
    # plugins/prompt.py
    from typing import Dict, Any
    from chaoscypher_core.services.workflows.tools.plugins import ToolExecutionContext

    class PromptPlugin:
        @property
        def tool_id(self) -> str:
            return "ai.prompt"

        @property
        def category(self) -> str:
            return "ai"

        @property
        def name(self) -> str:
            return "AI Prompt"

        @property
        def description(self) -> str:
            return "Execute AI prompts with optional chunking"

        @property
        def input_schema(self) -> Dict[str, Any]:
            return {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "system_prompt": {"type": "string"},
                    "output_format": {"type": "string", "enum": ["text", "json"]}
                },
                "required": ["prompt"]
            }

        async def execute(
            self,
            inputs: Dict[str, Any],
            context: ToolExecutionContext
        ) -> Dict[str, Any]:
            # Implementation here
            return {"result": "..."}
    ```

Usage:
    from chaoscypher_core.services.workflows.tools.engine import BaseToolPlugin, ToolRegistry

    # Auto-discover and register plugins
    registry = ToolRegistry()

    # Get plugin
    plugin = registry.get("ai.prompt")

    # Execute
    result = await plugin.execute(inputs, context)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol


if TYPE_CHECKING:
    from chaoscypher_core.plugins import PluginMetadata
    from chaoscypher_core.services.workflows.tools.engine.context import ToolExecutionContext


class BaseToolPlugin(Protocol):
    """Protocol defining the interface for workflow tool plugins.

    All plugins must implement this interface to be discovered and executed
    by the tool system. Uses Protocol for structural subtyping (no inheritance).

    Properties:
        metadata: Plugin metadata (optional, for standardized info)
        tool_id: Unique identifier (format: "category.tool_name")
        category: Tool category ("ai", "data", "logic", "http", etc.)
        name: Human-readable name
        description: Tool purpose and usage
        input_schema: JSON schema for input validation

    Methods:
        execute: Execute the tool with validated inputs
    """

    @property
    def metadata(self) -> PluginMetadata:
        """Get plugin metadata (optional).

        Returns:
            PluginMetadata instance with tool information.

        Note:
            This property is optional for backwards compatibility.
            New tools should implement it for consistent metadata.
        """
        ...

    @property
    def tool_id(self) -> str:
        """Unique tool identifier in format "category.tool_name".

        Examples:
            - "ai.prompt"
            - "data.extract"
            - "logic.conditional"

        Returns:
            Dot-separated tool identifier

        """
        ...

    @property
    def category(self) -> str:
        """Tool category for organization and routing.

        Categories:
            - "ai": LLM and AI operations
            - "data": Data transformation
            - "logic": Control flow
            - "http": HTTP requests
            - "graph": Graph operations
            - "template": Template operations

        Returns:
            Category name (lowercase)

        """
        ...

    @property
    def icon(self) -> str:
        """MUI icon name for UI display.

        Returns:
            MUI icon component name (e.g. "SmartToy", "Http")

        """
        ...

    @property
    def name(self) -> str:
        """Human-readable tool name for UI display.

        Examples:
            - "AI Prompt"
            - "Extract Data"
            - "Conditional Branch"

        Returns:
            Display name (title case)

        """
        ...

    @property
    def description(self) -> str:
        """Brief description of tool purpose and behavior.

        Should explain:
        - What the tool does
        - When to use it
        - Key features or constraints

        Returns:
            One-sentence description

        """
        ...

    @property
    def input_schema(self) -> dict[str, Any]:
        """JSON schema for input validation.

        Defines:
        - Required and optional parameters
        - Parameter types
        - Validation rules (enums, patterns, ranges)
        - Default values

        Returns:
            JSON schema dict (Draft 7 spec)

        Example:
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100}
                },
                "required": ["query"]
            }

        """
        ...

    @property
    def output_schema(self) -> dict[str, Any]:
        """JSON schema describing tool output structure.

        Defines:
        - Output field names and types
        - Field descriptions
        - Required vs optional outputs

        Returns:
            JSON schema dict (Draft 7 spec)

        Example:
            {
                "type": "object",
                "properties": {
                    "result": {"type": "string", "description": "Generated text"},
                    "model": {"type": "string", "description": "Model used"},
                    "tokens_used": {"type": "integer", "description": "Tokens consumed"}
                },
                "required": ["result"]
            }

        """
        ...

    async def execute(
        self, inputs: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any]:
        """Execute the tool with validated inputs.

        This method contains the core tool logic. Inputs are pre-validated
        against input_schema before execution.

        Args:
            inputs: Validated input parameters (matches input_schema)
            context: Execution context with services and workflow state

        Returns:
            Execution results as dictionary

        Raises:
            ValueError: If inputs invalid (should not happen - pre-validated)
            RuntimeError: If required services unavailable
            Exception: Any tool-specific errors

        Example:
            async def execute(self, inputs, context):
                query = inputs["query"]
                limit = inputs.get("limit", 10)

                # Use services from context
                results = await context.graph_manager.search(query, limit)

                return {
                    "results": results,
                    "count": len(results)
                }

        """
        ...


__all__ = ["BaseToolPlugin"]
