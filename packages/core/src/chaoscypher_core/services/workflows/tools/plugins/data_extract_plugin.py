# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Data Extract Plugin - Extract nested data using dot notation.

Extracts data from nested objects using dot notation paths.
Supports dictionaries and lists with array index notation.

Extracted from executors/data_executor.py and converted to plugin architecture.
"""

from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.plugins import ToolExecutionContext

logger = structlog.get_logger(__name__)


class ExtractPlugin:
    """Data Extract tool plugin.

    Extract data from nested objects using dot notation (e.g., "user.name",
    "items.0.title"). Supports dictionaries and lists with array indices.
    """

    @property
    def tool_id(self) -> str:
        """Stub implementation - not yet implemented."""
        return "data.extract"

    @property
    def category(self) -> str:
        """Stub implementation - not yet implemented."""
        return "data"

    @property
    def icon(self) -> str:
        """MUI icon name for UI display."""
        return "FilterAlt"

    @property
    def name(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Extract Data"

    @property
    def description(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Extract data from nested object using dot notation path"

    @property
    def input_schema(self) -> dict[str, Any]:
        """Stub implementation - not yet implemented."""
        return {
            "type": "object",
            "properties": {
                "source": {"description": "Source object to extract from"},
                "path": {
                    "type": "string",
                    "description": "Dot notation path (e.g., 'user.name', 'items.0.title')",
                },
                "default": {"description": "Default value if path not found"},
            },
            "required": ["source", "path"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        """Output schema for Extract Data tool."""
        return {
            "type": "object",
            "properties": {
                "value": {
                    "description": "Extracted value from the path",
                },
                "found": {
                    "type": "boolean",
                    "description": "Whether the path was found in the source",
                },
            },
            "required": ["value", "found"],
        }

    async def execute(
        self, inputs: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any]:
        """Extract data using dot notation path.

        Args:
            inputs: Tool inputs (source, path, default)
            context: Execution context

        Returns:
            Dictionary with extracted value and found flag

        """
        source = inputs["source"]
        path = inputs["path"]
        default_value = inputs.get("default")

        # Navigate through path
        value = source
        found = True

        try:
            for key in path.split("."):
                if isinstance(value, dict):
                    value = value[key]
                elif isinstance(value, list):
                    # Support array index notation
                    value = value[int(key)]
                else:
                    found = False
                    value = default_value
                    break
        except (KeyError, IndexError, ValueError, TypeError):  # fmt: skip
            found = False
            value = default_value

        return {"value": value, "found": found}


__all__ = ["ExtractPlugin"]
