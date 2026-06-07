# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Schema building and structured extraction for LLM function calling.

Provides provider-specific schema conversion (OpenAI, Anthropic, Gemini, Ollama)
and structured data extraction with validation and repair.

- build_extraction_tools: Dispatches to per-provider schema builders
- StructuredExtractor: Provider-agnostic structured data extraction
- Per-provider builders: OpenAISchemaBuilder, AnthropicSchemaBuilder, etc.

Note: ``TaskType`` now lives in ``chaoscypher_core.ports.llm``. Import it
from there rather than from this subpackage.
"""

# Per-provider schema builders
from chaoscypher_core.adapters.llm.schema.anthropic import AnthropicSchemaBuilder
from chaoscypher_core.adapters.llm.schema.base import build_extraction_tools
from chaoscypher_core.adapters.llm.schema.extractor import StructuredExtractor
from chaoscypher_core.adapters.llm.schema.gemini import GeminiSchemaBuilder
from chaoscypher_core.adapters.llm.schema.ollama import OllamaSchemaBuilder
from chaoscypher_core.adapters.llm.schema.openai import OpenAISchemaBuilder


__all__ = [
    "AnthropicSchemaBuilder",
    "GeminiSchemaBuilder",
    "OllamaSchemaBuilder",
    "OpenAISchemaBuilder",
    "StructuredExtractor",
    "build_extraction_tools",
]
