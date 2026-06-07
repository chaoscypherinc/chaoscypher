# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Loop Plugin - Loop/iteration control flow.

Iterates over collections with optional max iterations limit.
Returns collection items and iteration count.

Extracted from executors/logic_executor.py and converted to plugin architecture.
"""

from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.plugins import ToolExecutionContext

logger = structlog.get_logger(__name__)


class LoopPlugin:
    """Loop tool plugin.

    Iterate over collections with max iteration limit. Returns the collection
    items and count. The workflow engine handles actual iteration.
    """

    @property
    def tool_id(self) -> str:
        """Stub implementation - not yet implemented."""
        return "logic.loop"

    @property
    def category(self) -> str:
        """Stub implementation - not yet implemented."""
        return "logic"

    @property
    def icon(self) -> str:
        """MUI icon name for UI display."""
        return "Loop"

    @property
    def name(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Loop"

    @property
    def description(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Iterate over collection with optional max iterations"

    @property
    def input_schema(self) -> dict[str, Any]:
        """Stub implementation - not yet implemented."""
        return {
            "type": "object",
            "properties": {
                "collection": {"type": "array", "description": "Collection to iterate over"},
                "iterator_name": {
                    "type": "string",
                    "description": "Name for iterator variable",
                    "default": "item",
                },
                "max_iterations": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum iterations (defaults to collection length)",
                },
            },
            "required": ["collection"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        """Output schema for Loop tool."""
        return {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "description": "Items to iterate over (up to max_iterations)",
                },
                "iterations": {
                    "type": "integer",
                    "description": "Number of iterations to perform",
                },
            },
            "required": ["results", "iterations"],
        }

    async def execute(
        self, inputs: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any]:
        """Prepare loop iteration data.

        Note: The workflow engine handles actual iteration. This tool just
        returns the collection info for the engine to process.

        Args:
            inputs: Tool inputs (collection, iterator_name, max_iterations)
            context: Execution context

        Returns:
            Dictionary with results and iteration count

        """
        collection = inputs["collection"]
        inputs.get("iterator_name", "item")
        max_iterations = inputs.get("max_iterations", len(collection))

        return {
            "results": collection[:max_iterations],
            "iterations": min(len(collection), max_iterations),
        }


__all__ = ["LoopPlugin"]
