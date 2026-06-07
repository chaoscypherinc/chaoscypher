# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Data Merge Plugin - Merge multiple objects/dictionaries.

Merges multiple objects with shallow or deep merge strategies.

Extracted from executors/data_executor.py and converted to plugin architecture.
"""

import copy
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.plugins import ToolExecutionContext

logger = structlog.get_logger(__name__)


class MergePlugin:
    """Data Merge tool plugin.

    Merge multiple objects/dictionaries. Supports shallow and deep merge
    strategies. Later objects override earlier ones.
    """

    @property
    def tool_id(self) -> str:
        """Stub implementation - not yet implemented."""
        return "data.merge"

    @property
    def category(self) -> str:
        """Stub implementation - not yet implemented."""
        return "data"

    @property
    def icon(self) -> str:
        """MUI icon name for UI display."""
        return "MergeType"

    @property
    def name(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Merge Data"

    @property
    def description(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Merge multiple objects with shallow or deep strategies"

    @property
    def input_schema(self) -> dict[str, Any]:
        """Stub implementation - not yet implemented."""
        return {
            "type": "object",
            "properties": {
                "objects": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of objects to merge",
                },
                "strategy": {
                    "type": "string",
                    "enum": ["shallow", "deep"],
                    "description": "Merge strategy",
                    "default": "shallow",
                },
            },
            "required": ["objects"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        """Output schema for Merge Data tool."""
        return {
            "type": "object",
            "properties": {
                "result": {
                    "type": "object",
                    "description": "Merged result object",
                },
            },
            "required": ["result"],
        }

    async def execute(
        self, inputs: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any]:
        """Merge objects using specified strategy.

        Args:
            inputs: Tool inputs (objects, strategy)
            context: Execution context

        Returns:
            Dictionary with merged result

        """
        objects = inputs["objects"]
        strategy = inputs.get("strategy", "shallow")

        if strategy == "shallow":
            # Shallow merge
            result = {}
            for obj in objects:
                if isinstance(obj, dict):
                    result.update(obj)
        else:
            # Deep merge
            result = {}
            for obj in objects:
                if isinstance(obj, dict):
                    result = self._deep_merge(result, copy.deepcopy(obj))

        return {"result": result}

    def _deep_merge(self, dict1: dict, dict2: dict) -> dict:
        """Deep merge two dictionaries."""
        result = dict1.copy()
        for key, value in dict2.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result


__all__ = ["MergePlugin"]
