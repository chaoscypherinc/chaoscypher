# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""OpenAI schema builder for LLM function calling.

Builds OpenAI-compatible tool schemas for structured data extraction.
"""

from typing import Any


class OpenAISchemaBuilder:
    """Build OpenAI function calling format schemas.

    OpenAI expects:
    {
        "type": "function",
        "function": {
            "name": "tool_name",
            "description": "...",
            "parameters": { ...json_schema... }
        }
    }
    """

    @staticmethod
    def build(tool_name: str, json_schema: dict[str, Any]) -> list[dict[str, Any]]:
        """Build OpenAI function calling format schema.

        Args:
            tool_name: Name of the extraction tool
            json_schema: User's JSON schema for the data structure

        Returns:
            List of tool definitions in OpenAI format

        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": "Extract structured data from the provided text according to the schema.",
                    "parameters": {
                        "type": "object",
                        "properties": {"data": json_schema},
                        "required": ["data"],
                        "additionalProperties": False,
                    },
                },
            }
        ]
