# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Ollama schema builder for LLM function calling.

Builds Ollama-compatible tool schemas for structured data extraction.
"""

from typing import Any


class OllamaSchemaBuilder:
    """Build Ollama function calling format schemas.

    Note: Must have 'title' and 'description' at top level for LangChain validation.

    Ollama expects OpenAI format but with additional metadata:
    {
        "type": "function",
        "function": {
            "name": "tool_name",
            "description": "...",
            "parameters": {
                "type": "object",
                "title": "...",
                "description": "...",
                ...
            }
        }
    }
    """

    @staticmethod
    def build(tool_name: str, json_schema: dict[str, Any]) -> list[dict[str, Any]]:
        """Build Ollama function calling format schema.

        Args:
            tool_name: Name of the extraction tool
            json_schema: User's JSON schema for the data structure

        Returns:
            List of tool definitions in Ollama format

        """
        schema_with_metadata = {
            "type": "object",
            "title": "ExtractedData",
            "description": "Structured data extracted from text",
            "properties": {"data": json_schema},
            "required": ["data"],
        }

        return [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": "Extract structured data from the provided text according to the schema.",
                    "parameters": schema_with_metadata,
                },
            }
        ]
