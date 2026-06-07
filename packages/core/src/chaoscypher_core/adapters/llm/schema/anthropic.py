# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Anthropic schema builder for LLM function calling.

Builds Anthropic Claude-compatible tool schemas for structured data extraction.
"""

from typing import Any


class AnthropicSchemaBuilder:
    """Build Anthropic Claude tool format schemas.

    Anthropic expects:
    {
        "name": "tool_name",
        "description": "...",
        "input_schema": { ...json_schema... }
    }
    """

    @staticmethod
    def build(tool_name: str, json_schema: dict[str, Any]) -> list[dict[str, Any]]:
        """Build Anthropic Claude tool format schema.

        Args:
            tool_name: Name of the extraction tool
            json_schema: User's JSON schema for the data structure

        Returns:
            List of tool definitions in Anthropic format

        """
        return [
            {
                "name": tool_name,
                "description": "Extract structured data from the provided text according to the schema.",
                "input_schema": {
                    "type": "object",
                    "properties": {"data": json_schema},
                    "required": ["data"],
                },
            }
        ]
