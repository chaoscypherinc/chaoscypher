# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Provider schema building for LLM function calling.

Builds provider-specific tool schemas for LLM function calling.
Dispatches to per-provider schema builders (OpenAI, Anthropic, Gemini, Ollama).

SRP: Single responsibility for schema generation across different LLM providers.
"""

from typing import Any

import structlog

from chaoscypher_core.adapters.llm.schema.anthropic import AnthropicSchemaBuilder
from chaoscypher_core.adapters.llm.schema.gemini import GeminiSchemaBuilder
from chaoscypher_core.adapters.llm.schema.ollama import OllamaSchemaBuilder
from chaoscypher_core.adapters.llm.schema.openai import OpenAISchemaBuilder


logger = structlog.get_logger(__name__)


def build_extraction_tools(
    tool_name: str, json_schema: dict[str, Any], provider: str
) -> list[dict[str, Any]]:
    """Build provider-specific tools payload for forced JSON extraction.

    Dispatches to per-provider schema builders (OpenAI, Anthropic, Gemini, Ollama).
    Falls back to OpenAI format for unknown providers.

    This is the ONE SOURCE OF TRUTH for schema building.
    Used by both Docker backend (via tool system) and CLI (direct usage).

    Args:
        tool_name: Name of the extraction tool
        json_schema: User's JSON schema for the data structure
        provider: LLM provider name ('openai', 'anthropic', 'google', 'gemini', 'ollama')

    Returns:
        List of tool definitions in provider-specific format

    Example:
        >>> schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        >>> tools = build_extraction_tools("ExtractData", schema, "openai")

    """
    provider = provider.lower()

    if provider == "openai":
        return OpenAISchemaBuilder.build(tool_name, json_schema)

    if provider == "anthropic":
        return AnthropicSchemaBuilder.build(tool_name, json_schema)

    if provider in ["google", "gemini"]:
        return GeminiSchemaBuilder.build(tool_name, json_schema)

    if provider == "ollama":
        return OllamaSchemaBuilder.build(tool_name, json_schema)

    # Fallback to OpenAI format (most widely supported)
    logger.warning("unknown_provider_fallback", provider=provider, fallback_format="openai")
    return OpenAISchemaBuilder.build(tool_name, json_schema)
