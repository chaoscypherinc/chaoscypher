# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""AI JSON Extraction Plugin - Structured Data Extraction.

Extracts structured JSON from text using LLM with schema validation. Supports
quality checking, retries, and JSON schema validation.

Restored from deleted json_extraction.py and converted to plugin architecture.
"""

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import OperationError, ValidationError


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.plugins import ToolExecutionContext

logger = structlog.get_logger(__name__)


class ExtractJsonPlugin:
    """AI Extract JSON tool plugin.

    Extract structured JSON from text using LLM with schema validation.
    Delegates to engine's StructuredExtractor for consistency and reliability.
    """

    @property
    def tool_id(self) -> str:
        """Stub implementation - not yet implemented."""
        return "ai.extract_json"

    @property
    def category(self) -> str:
        """Stub implementation - not yet implemented."""
        return "ai"

    @property
    def icon(self) -> str:
        """MUI icon name for UI display."""
        return "DataObject"

    @property
    def name(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Extract JSON"

    @property
    def description(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Extract structured JSON from text using LLM with schema validation"

    @property
    def input_schema(self) -> dict[str, Any]:
        """Stub implementation - not yet implemented."""
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to extract data from"},
                "json_schema": {
                    "type": "object",
                    "description": "JSON schema defining expected structure",
                },
                "system_prompt": {
                    "type": "string",
                    "description": "Optional system prompt (default: extraction expert)",
                },
                "user_instructions": {
                    "type": "string",
                    "description": "Optional additional instructions",
                },
                "temperature": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 2.0,
                    "description": "LLM temperature (uses extraction_temperature from settings if not specified)",
                },
                "max_retries": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Max retry attempts (default: from settings)",
                },
                "max_tokens": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Max tokens to generate (default: from settings extraction_max_tokens)",
                    "default": 16384,
                },
                "enable_quality_check": {
                    "type": "boolean",
                    "description": "Enable schema validation (default: True)",
                    "default": True,
                },
            },
            "required": ["text", "json_schema"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        """Output schema for Extract JSON tool."""
        return {
            "type": "object",
            "properties": {
                "extracted_data": {
                    "type": "object",
                    "description": "Extracted data matching the provided JSON schema",
                },
                "_metadata": {
                    "type": "object",
                    "description": "Extraction metadata",
                    "properties": {
                        "success": {
                            "type": "boolean",
                            "description": "Whether extraction succeeded",
                        },
                        "attempts": {
                            "type": "integer",
                            "description": "Number of extraction attempts",
                        },
                        "model": {
                            "type": "string",
                            "description": "Model used for extraction",
                        },
                    },
                },
            },
            "required": ["extracted_data"],
        }

    async def execute(
        self, inputs: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any]:
        """Extract structured JSON from text using LLM with schema validation.

        Delegates all extraction logic to engine's StructuredExtractor for
        consistency across the codebase. Supports quality checking, retries,
        and JSON schema validation.

        Args:
            inputs: Tool inputs containing:
                - text: Text to extract data from
                - json_schema: JSON schema defining expected structure
                - system_prompt: Optional system prompt (default: extraction expert)
                - user_instructions: Optional additional instructions
                - temperature: LLM temperature (default: 0.1 for consistency)
                - max_retries: Max retry attempts (default: from settings)
                - max_tokens: Max tokens to generate (default: 8192)
                - enable_quality_check: Enable schema validation (default: True)
            context: Execution context with services

        Returns:
            Dictionary with:
                - Extracted data matching json_schema
                - _metadata: Extraction metadata (success, attempts, etc.)

        Raises:
            OperationError: If LLM service not available
            ValidationError: If engine settings not provided
            Propagates exceptions from StructuredExtractor

        Example:
            >>> schema = {
            ...     "type": "object",
            ...     "properties": {
            ...         "entities": {"type": "array", "items": {"type": "object"}}
            ...     }
            ... }
            >>> result = await execute(
            ...     inputs={'text': 'Alice works at Acme...', 'json_schema': schema},
            ...     context=context
            ... )
            >>> entities = result.get('entities', [])
            >>> print(f"Extracted {len(entities)} entities")

        Note:
            - Uses engine.adapters.llm.StructuredExtractor as single source of truth
            - Low temperature (default 0.1 from settings) for consistent extraction
            - Quality checking validates against schema after extraction

        """
        if not context.llm_service:
            raise OperationError(
                "AI tools require LLM service",
                operation="ai.extract_json",
            )

        # Get settings from context (needed for LLM config defaults below)
        if not context.settings:
            raise ValidationError(
                "Engine settings not provided in context - required for LLM operations",
                field="settings",
            )

        # Extract inputs
        text = inputs["text"]
        json_schema = inputs["json_schema"]
        system_prompt = inputs.get(
            "system_prompt", "You are an expert at structured data extraction."
        )
        user_instructions = inputs.get("user_instructions", "")
        max_tokens = inputs.get("max_tokens", context.settings.llm.extraction_max_tokens)
        enable_quality_check = inputs.get("enable_quality_check", True)

        temperature = inputs.get("temperature")
        if temperature is None:
            temperature = context.settings.llm.extraction_temperature

        max_retries = inputs.get("max_retries")

        logger.info(
            "ai_extract_json_delegating_to_engine",
            text_length=len(text),
            enable_quality_check=enable_quality_check,
        )

        # Prefer the port injected on the context; fall back to a one-shot
        # StructuredExtractor build for non-Engine callers. The adapter
        # imports are allowlisted under CC012 as factory wiring.
        extractor = context.structured_extractor
        if extractor is None:
            from chaoscypher_core.adapters.llm import ProviderFactory, StructuredExtractor

            provider_factory = ProviderFactory(context.settings)
            extractor = StructuredExtractor(provider_factory)

        # Get optional metrics collector from inputs (for per-call LLM metrics tracking)
        metrics_collector = inputs.get("metrics_collector")

        # Build kwargs - only pass max_retries if explicitly provided in inputs
        extract_kwargs: dict[str, Any] = {
            "text": text,
            "json_schema": json_schema,
            "system_prompt": system_prompt,
            "user_instructions": user_instructions,
            "temperature": float(temperature),
            "max_tokens": max_tokens,
            "enable_quality_check": enable_quality_check,
            "metrics_collector": metrics_collector,
        }
        if max_retries is not None:
            extract_kwargs["max_retries"] = int(max_retries)

        # Call engine core (all business logic happens here)
        result = await extractor.extract_structured(**extract_kwargs)

        logger.info(
            "ai_extract_json_engine_completed",
            success=result.get("_metadata", {}).get("success", False),
            attempts=result.get("_metadata", {}).get("attempts", 0),
        )

        return result


__all__ = ["ExtractJsonPlugin"]
