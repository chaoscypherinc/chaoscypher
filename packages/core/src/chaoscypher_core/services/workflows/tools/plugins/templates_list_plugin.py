# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template List Plugin - List graph templates.

Lists all templates from the graph with optional system template filtering.

Extracted from executors/template_executor.py and converted to plugin architecture.
"""

from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.plugins import ToolExecutionContext

logger = structlog.get_logger(__name__)


class TemplateListPlugin:
    """Template List tool plugin.

    List all templates from the knowledge graph. Can filter out system
    templates to show only user-created templates.
    """

    @property
    def tool_id(self) -> str:
        """Stub implementation - not yet implemented."""
        return "templates.list"

    @property
    def category(self) -> str:
        """Stub implementation - not yet implemented."""
        return "templates"

    @property
    def icon(self) -> str:
        """MUI icon name for UI display."""
        return "ListAlt"

    @property
    def name(self) -> str:
        """Stub implementation - not yet implemented."""
        return "List Templates"

    @property
    def description(self) -> str:
        """Stub implementation - not yet implemented."""
        return "List all graph templates with optional system template filtering"

    @property
    def input_schema(self) -> dict[str, Any]:
        """Stub implementation - not yet implemented."""
        return {
            "type": "object",
            "properties": {
                "include_system": {
                    "type": "boolean",
                    "description": "Include system templates in results",
                    "default": True,
                }
            },
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        """Output schema for List Templates tool."""
        return {
            "type": "object",
            "properties": {
                "templates": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of template objects",
                },
            },
            "required": ["templates"],
        }

    async def execute(
        self, inputs: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any]:
        """List templates from graph.

        Args:
            inputs: Tool inputs (include_system)
            context: Execution context with graph manager

        Returns:
            Dictionary with templates array

        """
        include_system = inputs.get("include_system", True)
        templates = context.graph_manager.list_templates()

        if not include_system:
            templates = [t for t in templates if not t.is_system]

        return {"templates": [t.model_dump(mode="json") for t in templates]}


__all__ = ["TemplateListPlugin"]
