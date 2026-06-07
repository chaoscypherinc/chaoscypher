# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Structured Data Extractor.

Provider-agnostic structured data extraction with validation and repair.
Extracted from chaoscypher_core.services.workflows.tools.management.system_tools.executors.ai_executor

ONE SOURCE OF TRUTH for structured extraction - used by both Docker backend and CLI.

Features:
- Provider-specific schema conversion (OpenAI, Anthropic, Gemini, Ollama)
- Intelligent retry with validation feedback
- Advanced JSON repair for truncation/malformation
- Quality validation with 20% threshold
- Provider-agnostic function calling
- Default field handling
- Metadata tracking

Usage:
    from chaoscypher_core.adapters.llm.schema import StructuredExtractor
    from chaoscypher_core.adapters.llm.factory import ProviderFactory

    provider_factory = ProviderFactory(settings)
    extractor = StructuredExtractor(provider_factory)

    result = await extractor.extract_structured(
        text="Extract entities from this text...",
        json_schema={"type": "object", "properties": {...}},
        system_prompt="You are an expert extractor...",
        max_retries=5
    )
"""

import asyncio
import json
import re
import time
from typing import TYPE_CHECKING, Any, cast

import jsonschema
import structlog

from chaoscypher_core.adapters.llm.error_types import map_exception_to_error_type
from chaoscypher_core.adapters.llm.schema.base import build_extraction_tools
from chaoscypher_core.exceptions import ModelCapabilityError


if TYPE_CHECKING:
    from chaoscypher_core.adapters.llm.factory import ProviderFactory
    from chaoscypher_core.analytics.llm_metrics import LLMMetricsCollector
    from chaoscypher_core.settings import ExtractionSettings

logger = structlog.get_logger(__name__)


class StructuredExtractor:
    """Provider-agnostic structured data extraction.

    This is the SINGLE SOURCE OF TRUTH for structured extraction logic.
    Both Docker backend (via tool system) and CLI use this directly.
    """

    def __init__(
        self,
        provider_factory: ProviderFactory,
        extraction_settings: ExtractionSettings | None = None,
    ):
        """Initialize structured extractor.

        Args:
            provider_factory: ProviderFactory instance for getting chat providers
            extraction_settings: Optional extraction config (uses defaults if not provided)

        """
        from chaoscypher_core.settings import ExtractionSettings

        self.provider_factory = provider_factory
        self._extraction_settings = extraction_settings or ExtractionSettings()

    @staticmethod
    def _record_attempt_metrics(
        metrics_collector: LLMMetricsCollector,
        *,
        success: bool,
        input_tokens: int,
        output_tokens: int,
        duration_ms: int,
        attempt: int,
        text_len: int,
        retry_reason: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        entities_extracted: int | None = None,
        relationships_extracted: int | None = None,
    ) -> None:
        """Record a single extraction attempt to the metrics collector.

        Args:
            metrics_collector: Collector instance.
            success: Whether the attempt succeeded.
            input_tokens: Input tokens used.
            output_tokens: Output tokens used.
            duration_ms: Duration in milliseconds.
            attempt: Current attempt number (0-based).
            text_len: Character length of the input text.
            retry_reason: Reason for retry (if retrying).
            error_type: Error classification string.
            error_message: Human-readable error message.
            entities_extracted: Count of entities extracted (if applicable).
            relationships_extracted: Count of relationships extracted (if applicable).

        """
        metrics_collector.record_attempt(
            success=success,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            was_retry=attempt > 0,
            retry_reason=retry_reason,
            error_type=error_type,
            error_message=error_message,
            chunk_size_chars=text_len,
            entities_extracted=entities_extracted,
            relationships_extracted=relationships_extracted,
        )

    async def extract_structured(
        self,
        text: str,
        json_schema: dict[str, Any],
        system_prompt: str = "You are an expert at extracting structured information from text.",
        user_instructions: str | None = None,
        temperature: float | None = None,
        max_retries: int | None = None,
        max_tokens: int | None = None,
        enable_quality_check: bool = True,
        metrics_collector: LLMMetricsCollector | None = None,
    ) -> dict[str, Any]:
        """Extract structured data using provider-specific tool calling.

        Features:
        - Provider-agnostic (works with OpenAI, Anthropic, Gemini, Ollama)
        - Intelligent retry with validation feedback
        - Advanced JSON repair for truncation/malformation
        - Quality validation with 20% threshold
        - Default field handling
        - Metrics collection for retry/failure analysis

        Args:
            text: Input text to analyze
            json_schema: JSON schema for desired output structure
            system_prompt: System instructions for the LLM
            user_instructions: Additional user instructions (optional)
            temperature: LLM temperature (uses extraction_temperature from settings if None)
            max_retries: Maximum retry attempts (uses llm_max_retries from settings if None)
            max_tokens: Maximum tokens in response (uses extraction_max_tokens from settings if None)
            enable_quality_check: Enable quality validation (default True)
            metrics_collector: Optional collector for per-call metrics tracking

        Returns:
            Extracted structured data matching schema with _metadata field

        Raises:
            ValueError: If schema is invalid or extraction fails after retries

        """
        # Resolve defaults from settings if not explicitly provided
        if temperature is None:
            temperature = self.provider_factory.settings.llm.extraction_temperature
        if max_retries is None:
            max_retries = self.provider_factory.settings.llm.llm_max_retries
        if max_tokens is None:
            max_tokens = self.provider_factory.settings.llm.extraction_max_tokens

        # Setup extraction context
        provider = self.provider_factory.get_chat_provider()
        provider_name = self.provider_factory.settings.llm.chat_provider.lower()
        enhanced_system_prompt = self._build_system_prompt(
            system_prompt, json_schema, enable_quality_check
        )
        user_prompt = self._build_user_prompt(text, user_instructions)
        tools_payload = build_extraction_tools("ExtractStructuredData", json_schema, provider_name)

        logger.info(
            "structured_extraction_starting",
            provider=provider_name,
            text_length=len(text),
            enable_quality_check=enable_quality_check,
            has_metrics_collector=metrics_collector is not None,
            collector_id=id(metrics_collector) if metrics_collector else None,
        )

        # Track metrics across retries
        total_input_tokens = 0
        total_output_tokens = 0
        total_llm_calls = 0
        validation_errors: list[str] = []

        # Retry loop with validation feedback
        for attempt in range(max_retries + 1):
            attempt_start = time.time()
            try:
                (
                    result,
                    extracted_data,
                    input_tokens,
                    output_tokens,
                ) = await self._attempt_extraction(
                    provider=provider,
                    enhanced_system_prompt=enhanced_system_prompt,
                    user_prompt=user_prompt,
                    tools_payload=tools_payload,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    provider_name=provider_name,
                    attempt=attempt,
                    max_retries=max_retries,
                )
                attempt_duration_ms = int((time.time() - attempt_start) * 1000)
                total_llm_calls += 1
                total_input_tokens += input_tokens
                total_output_tokens += output_tokens

                # Common metrics for all recording calls in this try block
                base_metrics: dict[str, Any] = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "duration_ms": attempt_duration_ms,
                    "attempt": attempt,
                    "text_len": len(text),
                }

                # Validate, quality-check, and return or signal retry
                outcome = self._process_successful_attempt(
                    extracted_data=extracted_data,
                    json_schema=json_schema,
                    enable_quality_check=enable_quality_check,
                    validation_errors=validation_errors,
                    attempt=attempt,
                    max_retries=max_retries,
                    metrics_collector=metrics_collector,
                    base_metrics=base_metrics,
                )

                if outcome["action"] == "retry":
                    user_prompt = self._strip_previous_retry_messages(user_prompt)
                    user_prompt += outcome["retry_prompt"]
                    continue
                if outcome["action"] == "return_with_errors":
                    return self._build_result_output(
                        extracted_data,
                        result.get("model", "unknown"),
                        validation_errors,
                        total_llm_calls,
                        total_input_tokens,
                        total_output_tokens,
                        success=False,
                    )

                # Success
                logger.info("structured_extraction_success", llm_calls=total_llm_calls)
                return self._build_result_output(
                    extracted_data,
                    result.get("model", "unknown"),
                    [],
                    total_llm_calls,
                    total_input_tokens,
                    total_output_tokens,
                    success=True,
                )

            except Exception as e:
                retry_prompt = await self._handle_extraction_error(
                    error=e,
                    attempt_start=attempt_start,
                    attempt=attempt,
                    max_retries=max_retries,
                    text_len=len(text),
                    provider=provider,
                    metrics_collector=metrics_collector,
                    validation_errors=validation_errors,
                )
                if retry_prompt is not None:
                    user_prompt = self._strip_previous_retry_messages(user_prompt)
                    user_prompt += retry_prompt
                    continue

        # All retries exhausted
        msg = (
            f"Failed to extract valid JSON after {max_retries + 1} attempts. "
            f"Errors: {'; '.join(validation_errors)}"
        )
        raise RuntimeError(msg)

    def _process_successful_attempt(
        self,
        *,
        extracted_data: dict[str, Any],
        json_schema: dict[str, Any],
        enable_quality_check: bool,
        validation_errors: list[str],
        attempt: int,
        max_retries: int,
        metrics_collector: LLMMetricsCollector | None,
        base_metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate and quality-check a successful extraction attempt.

        Runs schema validation, auto-fixes entity descriptions, and
        performs quality checks. Returns an action dict indicating
        whether to retry, return with errors, or accept the result.

        Args:
            extracted_data: Data extracted from the LLM response.
            json_schema: JSON schema to validate against.
            enable_quality_check: Whether quality validation is active.
            validation_errors: Accumulated validation errors (mutated in place).
            attempt: Current attempt number (0-based).
            max_retries: Maximum retry attempts.
            metrics_collector: Optional collector for per-call metrics.
            base_metrics: Token/timing metrics for this attempt.

        Returns:
            Action dict with key ``"action"`` set to ``"success"``,
            ``"retry"`` (with ``"retry_prompt"``), or ``"return_with_errors"``.

        """
        # Add default fields
        self._add_default_fields(extracted_data)

        # Validate against schema
        validation_result = self._validate_schema(
            extracted_data, json_schema, validation_errors, attempt, max_retries
        )
        if validation_result and validation_result != "return_with_errors":
            if metrics_collector:
                self._record_attempt_metrics(
                    metrics_collector,
                    success=False,
                    **base_metrics,
                    retry_reason="schema_validation",
                    error_type="validation_error",
                    error_message="Schema validation failed",
                )
            return {"action": "retry", "retry_prompt": validation_result}
        if validation_result == "return_with_errors":
            if metrics_collector:
                self._record_attempt_metrics(
                    metrics_collector,
                    success=False,
                    **base_metrics,
                    retry_reason="schema_validation",
                    error_type="validation_error",
                    error_message="Schema validation failed (final)",
                )
            return {"action": "return_with_errors"}

        # Auto-fix common issues before quality check (avoids expensive retries)
        if isinstance(extracted_data, dict) and "entities" in extracted_data:
            fixes_made = self._auto_fix_entity_descriptions(extracted_data.get("entities", []))
            if fixes_made > 0:
                logger.info("structured_extraction_auto_fixed_descriptions", fixes_made=fixes_made)

        # Quality check for entities
        if (
            enable_quality_check
            and isinstance(extracted_data, dict)
            and "entities" in extracted_data
        ):
            quality_result = self._check_and_handle_quality(
                extracted_data, validation_errors, attempt, max_retries
            )
            if quality_result:
                if metrics_collector:
                    self._record_attempt_metrics(
                        metrics_collector,
                        success=False,
                        **base_metrics,
                        retry_reason="quality_issues",
                        error_type="quality_error",
                        error_message="Quality check failed",
                        entities_extracted=len(extracted_data.get("entities", [])),
                        relationships_extracted=len(extracted_data.get("relationships", [])),
                    )
                return {"action": "retry", "retry_prompt": quality_result}

        # Success -- record metrics
        if metrics_collector:
            entities_count = None
            relationships_count = None
            if isinstance(extracted_data, dict):
                entities_count = len(extracted_data.get("entities", []))
                relationships_count = len(extracted_data.get("relationships", []))
            self._record_attempt_metrics(
                metrics_collector,
                success=True,
                **base_metrics,
                entities_extracted=entities_count,
                relationships_extracted=relationships_count,
            )

        return {"action": "success"}

    async def _handle_extraction_error(
        self,
        *,
        error: Exception,
        attempt_start: float,
        attempt: int,
        max_retries: int,
        text_len: int,
        provider: Any,
        metrics_collector: LLMMetricsCollector | None,
        validation_errors: list[str],
    ) -> str | None:
        """Handle an exception raised during an extraction attempt.

        Records metrics, applies health-check backoff for empty responses,
        and decides whether to retry or propagate the error.

        Args:
            error: The exception that was raised.
            attempt_start: ``time.time()`` recorded before the attempt.
            attempt: Current attempt number (0-based).
            max_retries: Maximum retry attempts.
            text_len: Character length of the input text.
            provider: LLM provider instance (may support ``check_health``).
            metrics_collector: Optional collector for per-call metrics.
            validation_errors: Accumulated validation errors (mutated in place).

        Returns:
            A retry prompt string if the attempt should be retried, or
            ``None`` if the error is terminal (the exception is re-raised
            before returning in that case).

        Raises:
            ModelCapabilityError: Re-raised immediately without retry.
            Exception: Re-raised when all retries are exhausted.

        """
        attempt_duration_ms = int((time.time() - attempt_start) * 1000)

        # Extract tokens from exception if available (attached during extraction)
        exc_input_tokens = getattr(error, "input_tokens", 0)
        exc_output_tokens = getattr(error, "output_tokens", 0)

        exc_base_metrics: dict[str, Any] = {
            "input_tokens": exc_input_tokens,
            "output_tokens": exc_output_tokens,
            "duration_ms": attempt_duration_ms,
            "attempt": attempt,
            "text_len": text_len,
        }

        if isinstance(error, ModelCapabilityError):
            if metrics_collector:
                self._record_attempt_metrics(
                    metrics_collector,
                    success=False,
                    **exc_base_metrics,
                    retry_reason="exception",
                    error_type="capability_error",
                    error_message=str(error),
                )
            logger.exception(
                "structured_extraction_capability_error",
                error_type=map_exception_to_error_type(error),
                error_message=str(error),
                model=getattr(error, "model", None),
                capability=getattr(error, "capability", None),
            )
            raise error

        if metrics_collector:
            self._record_attempt_metrics(
                metrics_collector,
                success=False,
                **exc_base_metrics,
                retry_reason="exception",
                error_type=map_exception_to_error_type(error),
                error_message=str(error),
            )

        if attempt < max_retries:
            logger.warning(
                "structured_extraction_attempt_failed_retrying",
                error_type=map_exception_to_error_type(error),
                error_message=str(error),
                attempt=attempt + 1,
                max_attempts=max_retries + 1,
            )

            await self._backoff_on_empty_response(error, provider, attempt)

            validation_errors.append(f"Extraction error: {error!s}")
            return self._build_error_retry_prompt(error)

        # Final attempt failed
        logger.exception(
            "structured_extraction_all_attempts_exhausted",
            error_type=map_exception_to_error_type(error),
            error_message=str(error),
            total_attempts=max_retries + 1,
        )
        validation_errors.append(f"Extraction error: {error!s}")
        return None

    async def _backoff_on_empty_response(
        self,
        error: Exception,
        provider: Any,
        attempt: int,
    ) -> None:
        """Apply health-check backoff when the provider returns an empty response.

        Empty responses often indicate model overload or instability.
        If the provider supports ``check_health``, a health check
        determines whether to use exponential backoff (unhealthy) or
        a brief pause (healthy but empty).

        Args:
            error: The extraction error to inspect.
            provider: LLM provider instance (may support ``check_health``).
            attempt: Current attempt number (0-based).

        """
        error_msg = str(error).lower()
        if "empty response" not in error_msg or not hasattr(provider, "check_health"):
            return

        is_healthy = await provider.check_health()
        if not is_healthy:
            backoff_seconds = min(
                self._extraction_settings.llm_backoff_max_seconds,
                self._extraction_settings.llm_backoff_multiplier * (attempt + 1),
            )
            logger.warning(
                "provider_unhealthy_backing_off",
                backoff_seconds=backoff_seconds,
                attempt=attempt + 1,
            )
            await asyncio.sleep(backoff_seconds)
        else:
            logger.info(
                "provider_healthy_brief_pause",
                pause_seconds=self._extraction_settings.llm_healthy_pause_seconds,
                reason="empty_response_despite_healthy_provider",
            )
            await asyncio.sleep(self._extraction_settings.llm_healthy_pause_seconds)

    def _build_system_prompt(
        self, base_prompt: str, json_schema: dict[str, Any], enable_quality_check: bool
    ) -> str:
        """Build enhanced system prompt with quality requirements.

        Args:
            base_prompt: Base system prompt
            json_schema: JSON schema (checked for entity extraction)
            enable_quality_check: Whether to add quality requirements

        Returns:
            Enhanced system prompt

        """
        prompt = base_prompt

        if enable_quality_check and "entities" in str(json_schema):
            prompt += (
                "\n\nQuality Requirements:"
                "\n* All entity descriptions MUST end with proper punctuation (. ! ?)"
                "\n* Descriptions should be complete sentences (minimum 10 characters)"
                "\n* All required fields (name, type, description) must be filled"
                "\n* Use concise but complete descriptions - avoid unnecessary verbosity"
            )

        prompt += "\n\nRespond using the ExtractStructuredData tool."

        return prompt

    def _build_user_prompt(self, text: str, user_instructions: str | None) -> str:
        """Build user prompt with text and optional instructions.

        Args:
            text: Input text to analyze
            user_instructions: Optional additional instructions

        Returns:
            Formatted user prompt

        """
        prompt = "Extract structured information from the text inside <document> tags.\n\n"
        if user_instructions:
            prompt += f"<instructions>\n{user_instructions}\n</instructions>\n\n"
        prompt += f"<document>\n{text}\n</document>"
        return prompt

    async def _attempt_extraction(
        self,
        provider: Any,
        enhanced_system_prompt: str,
        user_prompt: str,
        tools_payload: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        provider_name: str,
        attempt: int,
        max_retries: int,
    ) -> tuple[dict[str, Any], dict[str, Any], int, int]:
        """Attempt a single extraction call.

        Args:
            provider: LLM provider instance
            enhanced_system_prompt: System prompt
            user_prompt: User prompt
            tools_payload: Tool definitions
            temperature: LLM temperature
            max_tokens: Maximum tokens
            provider_name: Provider name for logging
            attempt: Current attempt number
            max_retries: Maximum retries

        Returns:
            Tuple of (raw_result, extracted_data, input_tokens, output_tokens)

        """
        logger.info(
            "structured_extraction_attempt",
            attempt=attempt + 1,
            max_attempts=max_retries + 1,
        )

        messages = [
            {"role": "system", "content": enhanced_system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Cap temperature at extraction_temperature for deterministic tool calling
        extraction_temp = self.provider_factory.settings.llm.extraction_temperature
        tool_calling_temp = min(temperature, extraction_temp)
        if tool_calling_temp < temperature:
            logger.info(
                "structured_extraction_temperature_reduced",
                original_temperature=temperature,
                reduced_temperature=tool_calling_temp,
            )

        result = await provider.chat(
            messages=messages,
            tools=tools_payload,
            temperature=tool_calling_temp,
            max_tokens=max_tokens,
            enable_thinking=self.provider_factory.settings.llm.thinking_for_extraction,
            stream=False,
        )

        # Extract token counts from usage dict BEFORE extraction (which may fail)
        usage = result.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        # Attempt extraction - if it fails, attach tokens to exception for tracking
        try:
            extracted_data = self._extract_from_response(
                result, "ExtractStructuredData", provider_name
            )
        except Exception as e:
            # Attach token counts to exception so they can be tracked even on failure
            e.input_tokens = input_tokens  # type: ignore[attr-defined]
            e.output_tokens = output_tokens  # type: ignore[attr-defined]
            raise

        return result, extracted_data, input_tokens, output_tokens

    def _alias_field(
        self,
        data: dict[str, Any],
        target: str,
        aliases: list[str],
        name_patterns: list[str],
        shape_fields: list[list[str]] | None = None,
        skip_fields: list[str] | None = None,
        allow_string_list: bool = False,
        fallback_to_only_array: bool = False,
    ) -> bool:
        """Generic four-tier field aliasing with shape-based detection.

        Philosophy: "If the shoe fits, use it regardless of brand name."

        Tier 1: Exact alias match (e.g., "templates" -> "suggested_templates")
        Tier 2: Partial name match (e.g., field contains "template")
        Tier 3: Shape-based match (e.g., list with name+type fields)
        Tier 4: Last resort - use only remaining array field (if enabled)

        Args:
            data: Dict to modify in place
            target: Target field name to normalize to
            aliases: Exact field names to check first
            name_patterns: Substrings to search for in field names
            shape_fields: List of field groups for shape detection.
                          Item matches if it has at least one field from each group.
                          e.g., [["name", "label"], ["type", "kind"]] means
                          item must have (name OR label) AND (type OR kind)
            skip_fields: Fields to skip during shape detection
            allow_string_list: If True, string lists also match in shape detection
            fallback_to_only_array: If True and only one array remains, use it

        Returns:
            True if field was found and aliased, False otherwise

        """
        if target in data:
            return True

        skip = set(skip_fields or [])

        # Tier 1: Exact alias match
        for alias in aliases:
            if alias in data and isinstance(data[alias], list):
                data[target] = data.pop(alias)
                logger.info(
                    "structured_extraction_field_aliased", target=target, from_field=alias, tier=1
                )
                return True

        # Tier 2: Partial name match
        for key, value in list(data.items()):
            if key in skip or not isinstance(value, list):
                continue
            if any(pattern in key.lower() for pattern in name_patterns):
                data[target] = data.pop(key)
                logger.info(
                    "structured_extraction_field_aliased", target=target, from_field=key, tier=2
                )
                return True

        # Tier 3: Shape-based detection
        if shape_fields is not None:
            for key, value in list(data.items()):
                if key in skip or not isinstance(value, list) or not value:
                    continue
                first_item = value[0]
                # Check string list
                if allow_string_list and isinstance(first_item, str):
                    data[target] = data.pop(key)
                    logger.info(
                        "structured_extraction_field_aliased", target=target, from_field=key, tier=3
                    )
                    return True
                # Check dict shape - must have at least one field from each group
                if isinstance(first_item, dict):
                    matches_shape = all(
                        any(field in first_item for field in group) for group in shape_fields
                    )
                    if matches_shape:
                        data[target] = data.pop(key)
                        logger.info(
                            "structured_extraction_field_aliased",
                            target=target,
                            from_field=key,
                            tier=3,
                        )
                        return True

        # Tier 4: Last resort - use only remaining array field
        if fallback_to_only_array:
            array_fields = [
                (k, v) for k, v in data.items() if k not in skip and isinstance(v, list) and v
            ]
            if len(array_fields) == 1:
                key, _ = array_fields[0]
                data[target] = data.pop(key)
                logger.info(
                    "structured_extraction_field_aliased", target=target, from_field=key, tier=4
                )
                return True

        return False

    def _add_default_fields(self, extracted_data: Any) -> None:
        """Add default empty arrays/values for optional fields.

        Handles flat items schema format.
        Uses shape-based detection to handle LLM field name variance.

        Args:
            extracted_data: Extracted data to modify in place

        """
        if not isinstance(extracted_data, dict):
            return

        # Check if this is a flat items array schema response
        if "items" in extracted_data and isinstance(extracted_data["items"], list):
            # Flat schema - ensure items is present, no further aliasing needed
            # The items will be parsed by _parse_flat_items in AIEntityExtractor
            logger.debug(
                "structured_extraction_flat_items_detected",
                items_count=len(extracted_data["items"]),
            )
            return

        # Alias variant field names: LLM outputs often diverge from the
        # requested schema (e.g. "nodes" instead of "entities") even with
        # structured output enabled. Normalise to canonical keys here.
        # Entity aliasing: needs name-like + type-like fields.
        # Note: Don't use generic names like "items", "objects" - they conflict with template suggestion
        # Shape detection (Tier 3) will catch entity arrays with correct structure
        self._alias_field(
            data=extracted_data,
            target="entities",
            aliases=["extracted_entities", "entity_list", "nodes"],
            name_patterns=["entit"],
            shape_fields=[
                ["name", "entity_name", "label", "title"],
                ["type", "entity_type", "category", "kind"],
            ],
        )

        # Relationship aliasing: needs source-like + target-like fields (only if entities exist)
        if "entities" in extracted_data:
            found = self._alias_field(
                data=extracted_data,
                target="relationships",
                aliases=["relations", "edges", "connections", "links", "associations"],
                name_patterns=["relat", "edge", "connect", "link"],
                shape_fields=[
                    ["source", "from", "source_index", "from_index", "src"],
                    ["target", "to", "target_index", "to_index", "dest"],
                    # Also support name-based refs for flexibility
                    ["source_name", "from_name"],
                    ["target_name", "to_name"],
                ],
                skip_fields=["entities"],
            )
            # Default to empty array if not found
            if not found:
                extracted_data["relationships"] = []
                logger.info("structured_extraction_added_default_relationships")

        # Template aliasing: needs name-like field OR is string list
        # Skip entities/relationships to avoid stealing them
        # Tier 4 fallback enabled: if only one array remains, use it for templates
        self._alias_field(
            data=extracted_data,
            target="suggested_templates",
            aliases=[
                "templates",
                "suggestions",
                "template_suggestions",
                "recommended_templates",
                "template_list",
                "template_recommendations",
                "results",
                "output",
                "recommendations",
                "response",
            ],
            name_patterns=["template", "suggest", "recommend"],
            shape_fields=[
                ["name", "type", "template", "category", "label", "reason", "description"]
            ],
            skip_fields=["entities", "relationships"],
            allow_string_list=True,
            fallback_to_only_array=True,
        )

        # Fallback: Convert entity_types dict to suggested_templates array
        # LLM sometimes returns {"entity_types": {"Concept": 56, ...}} instead of templates
        if "suggested_templates" not in extracted_data:
            # Check common field names for entity type counts
            entity_types = (
                extracted_data.get("entity_types")
                or extracted_data.get("extracted_entity_types")
                or extracted_data.get("entityTypes")
            )
            if isinstance(entity_types, dict) and entity_types:
                # Just extract names - normalizer will add descriptions from lookup
                extracted_data["suggested_templates"] = [
                    {"name": name.capitalize()}
                    for name, count in entity_types.items()
                    if isinstance(count, (int, float)) and count > 0
                ]
                logger.info(
                    "structured_extraction_converted_entity_types_to_templates",
                    template_count=len(extracted_data["suggested_templates"]),
                )

        # Handle string arrays in suggested_templates (convert to objects)
        if "suggested_templates" in extracted_data and isinstance(
            extracted_data["suggested_templates"], list
        ):
            templates = extracted_data["suggested_templates"]
            if templates and isinstance(templates[0], str):
                # Convert string array to object array - normalizer will add descriptions
                extracted_data["suggested_templates"] = [{"name": t} for t in templates]
                logger.info(
                    "structured_extraction_converted_string_templates", count=len(templates)
                )

        # Add defaults for template suggestion responses (aliasing already normalized field name)
        if "suggested_templates" in extracted_data:
            if "primary_domain" not in extracted_data:
                extracted_data["primary_domain"] = "General"
                logger.info("structured_extraction_added_default_primary_domain")
            if "document_type" not in extracted_data:
                extracted_data["document_type"] = "reference"
                logger.info("structured_extraction_added_default_document_type")

    def _validate_schema(
        self,
        extracted_data: Any,
        json_schema: dict[str, Any],
        validation_errors: list[str],
        attempt: int,
        max_retries: int,
    ) -> str | None:
        """Validate extracted data against schema.

        Args:
            extracted_data: Data to validate
            json_schema: Schema to validate against
            validation_errors: List to append errors to
            attempt: Current attempt number
            max_retries: Maximum retries

        Returns:
            None if valid, "retry" with feedback prompt, or "return_with_errors"

        """
        try:
            jsonschema.validate(instance=extracted_data, schema=json_schema)
            logger.info("structured_extraction_validated", attempt=attempt + 1)
            return None
        except jsonschema.ValidationError as ve:
            error_path = ".".join(str(p) for p in ve.path) if ve.path else "root"
            error_msg = f"Schema validation failed: {ve.message} at path {error_path}"
            validation_errors.append(error_msg)
            logger.warning(
                "structured_extraction_validation_failed",
                error_message=ve.message,
                error_path=error_path,
            )

            if attempt < max_retries:
                guidance = self._build_validation_guidance(ve, error_path, error_msg)
                return (
                    f"\n\n[VALIDATION ERROR - Attempt {attempt + 1}/{max_retries + 1}]\n"
                    f"{error_msg}\n\nHow to fix:\n- " + "\n- ".join(guidance)
                )

            logger.exception(
                "structured_extraction_all_attempts_failed",
                total_attempts=max_retries + 1,
            )
            return "return_with_errors"

    def _build_validation_guidance(
        self, error: jsonschema.ValidationError, error_path: str, error_msg: str
    ) -> list[str]:
        """Build specific guidance based on validation error type.

        Args:
            error: Validation error
            error_path: Path to error in schema
            error_msg: Error message

        Returns:
            List of guidance strings

        """
        guidance = []

        if "is a required property" in error.message:
            missing_field = error.message.split("'")[1] if "'" in error.message else "unknown"
            guidance.append(f"Add the required '{missing_field}' field to your response")

        elif "is not of type" in error.message:
            expected_type = (
                error.message.split("is not of type '")[1].split("'")[0]
                if "is not of type" in error.message
                else "unknown"
            )
            guidance.append(f"Field at '{error_path}' must be type '{expected_type}'")

        elif "is not one of" in error.message:
            guidance.append(
                f"Field at '{error_path}' must use one of the allowed values from the schema"
            )

        elif "Additional properties are not allowed" in error.message:
            guidance.append(f"Remove extra fields not defined in schema at '{error_path}'")

        else:
            guidance.append(
                "Review the schema carefully and ensure all required fields match exactly"
            )

        # Add schema reminder for common issues
        if "relationships" in error_msg or "entities" in error_msg:
            guidance.append(
                "Remember: relationships use 'source' and 'target' (not source_index/target_index)"
            )

        return guidance

    def _check_and_handle_quality(
        self,
        extracted_data: dict[str, Any],
        validation_errors: list[str],
        attempt: int,
        max_retries: int,
    ) -> str | None:
        """Check entity quality and return retry prompt if needed.

        Args:
            extracted_data: Extracted data with entities
            validation_errors: List to append errors to
            attempt: Current attempt number
            max_retries: Maximum retries

        Returns:
            Retry prompt if quality issues warrant retry, None otherwise

        """
        quality_issues = self._check_entity_quality(extracted_data.get("entities", []))

        if not quality_issues:
            logger.info("structured_extraction_quality_check_passed")
            return None

        logger.warning(
            "structured_extraction_quality_issues_detected", issue_count=len(quality_issues)
        )

        entity_count = len(extracted_data.get("entities", []))
        issue_ratio = len(quality_issues) / max(entity_count, 1)

        if (
            issue_ratio > self._extraction_settings.quality_issue_threshold
            and attempt < max_retries
        ):
            logger.warning(
                "structured_extraction_significant_quality_issues",
                issue_ratio=f"{issue_ratio:.1%}",
                action="requesting_retry",
            )
            validation_errors.extend(quality_issues)

            issues_summary = "; ".join(quality_issues[:5])
            return (
                f"\n\n[Previous response had quality issues: {issues_summary}. "
                f"Please ensure all entities have complete descriptions ending with proper punctuation, "
                f"and all required fields (name, type, description) are filled.]"
            )

        # Minor issues - accept result
        logger.info(
            "structured_extraction_minor_quality_issues",
            issue_ratio=f"{issue_ratio:.1%}",
            action="accepting_result",
        )
        for issue in quality_issues:
            logger.debug("structured_extraction_quality_issue", issue=issue)

        return None

    def _auto_fix_entity_descriptions(self, entities: list[Any]) -> int:
        """Auto-fix common entity description issues.

        Fixes simple issues like missing punctuation to avoid expensive LLM retries.
        Modifies entities in place.

        Args:
            entities: List of entity dictionaries

        Returns:
            Number of fixes applied

        """
        fixes = 0

        for entity in entities:
            if not isinstance(entity, dict):
                continue

            desc = entity.get("description", "")
            if not desc or not isinstance(desc, str):
                continue

            # Fix missing end punctuation (only for descriptions above the
            # incomplete-description threshold)
            if (
                len(desc) > self._extraction_settings.entity_desc_incomplete_threshold
                and desc[-1] not in ".!?,\"')"
            ):
                # Add period to complete the sentence
                entity["description"] = desc.rstrip() + "."
                fixes += 1
                logger.debug(
                    "structured_extraction_fixed_punctuation",
                    entity_name=entity.get("name", "unknown"),
                    original_ending=desc[-10:] if len(desc) > 10 else desc,
                )

        return fixes

    def _check_entity_quality(self, entities: list[Any]) -> list[str]:
        """Check entities for quality issues.

        Args:
            entities: List of entity dictionaries

        Returns:
            List of quality issue descriptions

        """
        quality_issues = []

        for idx, entity in enumerate(entities):
            if not isinstance(entity, dict):
                continue

            name = entity.get("name", f"entity_{idx}")
            desc = entity.get("description", "")

            if desc:
                extraction_settings = self._extraction_settings
                # Description ends mid-sentence (no punctuation)
                if (
                    len(desc) > extraction_settings.entity_desc_incomplete_threshold
                    and desc[-1] not in ".!?,\"'"
                ):
                    quality_issues.append(
                        f"Entity '{name}' has incomplete description (no end punctuation)"
                    )

                # Description is suspiciously short
                if len(desc) < extraction_settings.entity_desc_min_length:
                    quality_issues.append(
                        f"Entity '{name}' has very short description ({len(desc)} chars)"
                    )
            else:
                quality_issues.append(f"Entity '{name}' has empty description")

            # Check required fields
            if not entity.get("name"):
                quality_issues.append(f"Entity at index {idx} missing 'name' field")
            if not entity.get("type"):
                quality_issues.append(f"Entity '{name}' missing 'type' field")

        return quality_issues

    def _build_error_retry_prompt(self, error: Exception, _current_prompt: str = "") -> str:
        """Build retry prompt for extraction errors.

        This method is careful NOT to add messages that would worsen token exhaustion
        issues, and strips previous error messages to prevent accumulation.

        Args:
            error: The exception that occurred
            current_prompt: Current prompt (used to strip previous error messages)

        Returns:
            Prompt addition for retry (empty string for non-actionable errors)

        """
        error_str = str(error)

        # Don't add error details for errors that indicate token/context exhaustion
        # Adding more text would only make the problem worse
        non_actionable_patterns = [
            "status code: 500",
            "unexpected end",
            "empty response",
            "token limit",
            "context length",
            "maximum context",
            "too long",
        ]
        error_lower = error_str.lower()
        if any(pattern in error_lower for pattern in non_actionable_patterns):
            logger.debug(
                "structured_extraction_skipping_error_prompt",
                reason="non_actionable_error",
                error_type=map_exception_to_error_type(error),
            )
            return ""  # Silent retry without adding to prompt

        was_truncated = getattr(error, "was_truncated", False)

        if was_truncated:
            logger.warning(
                "structured_extraction_truncation_retry",
                action="asking_llm_to_focus_on_key_entities",
            )
            return (
                "\n\n[IMPORTANT: Previous response was incomplete/truncated. "
                "Focus on extracting the MOST IMPORTANT entities and relationships only. "
                "Prioritize quality over quantity. Use concise descriptions.]"
            )

        # For actionable errors (validation, parsing), add a single error message
        # Note: Previous error messages are stripped before this is appended (see caller)
        return f"\n\n[Previous attempt failed: {error!s}. Please fix and try again.]"

    def _strip_previous_retry_messages(self, prompt: str) -> str:
        """Strip all previous retry/error messages from prompt.

        Prevents message accumulation across retries which can:
        - Waste context window space
        - Make token exhaustion issues worse
        - Confuse the model with repeated identical messages

        Args:
            prompt: Current prompt that may contain previous retry messages

        Returns:
            Prompt with retry messages removed

        """
        # Remove all types of retry messages that we add
        patterns = [
            # Error retry messages
            r"\n\n\[Previous attempt failed[^\]]*\]",
            # Truncation messages
            r"\n\n\[IMPORTANT: Previous response was incomplete[^\]]*\]",
            # Quality issue messages
            r"\n\n\[Previous response had quality issues[^\]]*\]",
            # Validation error messages (multi-line)
            r"\n\n\[VALIDATION ERROR - Attempt \d+/\d+\][^\[]*(?=\n\n|\Z)",
        ]
        cleaned = prompt
        for pattern in patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.DOTALL)
        return cleaned.rstrip()

    def _build_result_output(
        self,
        extracted_data: Any,
        model: str,
        validation_errors: list[str],
        total_llm_calls: int,
        total_input_tokens: int,
        total_output_tokens: int,
        success: bool,
    ) -> dict[str, Any]:
        """Build result dictionary with metadata.

        Args:
            extracted_data: Extracted data
            model: Model name
            validation_errors: List of validation errors
            total_llm_calls: Number of LLM calls made
            total_input_tokens: Total input/prompt tokens used
            total_output_tokens: Total output/completion tokens used
            success: Whether extraction succeeded

        Returns:
            Result dictionary with _metadata field

        """
        result_output: dict[str, Any] = {}
        if isinstance(extracted_data, dict):
            result_output.update(extracted_data)
        else:
            result_output["data"] = extracted_data

        result_output["_metadata"] = {
            "model": model,
            "validation_errors": validation_errors,
            "attempts": total_llm_calls,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "success": success,
        }

        return result_output

    def _extract_from_response(
        self, result: dict[str, Any], tool_name: str, provider: str
    ) -> dict[str, Any]:
        """Extract structured data from provider-specific response format.

        Args:
            result: LLM response containing tool calls or content
            tool_name: Expected tool name
            provider: Provider name for format detection

        Returns:
            Extracted structured data

        Raises:
            ValueError: If extraction fails

        """
        # Try tool calls first (structured output - guaranteed valid JSON)
        tool_calls = result.get("tool_calls")

        if tool_calls and len(tool_calls) > 0:
            logger.info(
                "structured_extraction_tool_call_detected",
                count=len(tool_calls),
                has_content=bool(result.get("content")),
            )
            logger.debug(
                "structured_extraction_tool_calls_structure",
                tool_calls_json=json.dumps(tool_calls, indent=2),
                content=result.get("content", ""),
            )

            tool_call = tool_calls[0]
            function_data = tool_call.get("function", {})
            arguments = function_data.get("arguments")

            # Handle arguments as dict (normal case)
            if isinstance(arguments, dict):
                extracted_data = arguments.get("data", arguments)
                logger.info(
                    "structured_extraction_extracted_from_tool_call",
                    data_keys=list(extracted_data.keys())
                    if isinstance(extracted_data, dict)
                    else None,
                )
                return cast("dict[str, Any]", extracted_data)

            # Handle arguments as string (some models return stringified JSON)
            if isinstance(arguments, str) and arguments.strip():
                logger.warning(
                    "structured_extraction_tool_args_as_string",
                    args_length=len(arguments),
                )
                try:
                    # Try to parse the string as JSON
                    parsed_args = json.loads(arguments)
                    extracted_data = parsed_args.get("data", parsed_args)
                    logger.info(
                        "structured_extraction_parsed_string_args",
                        data_keys=list(extracted_data.keys())
                        if isinstance(extracted_data, dict)
                        else None,
                    )
                    return cast("dict[str, Any]", extracted_data)
                except json.JSONDecodeError:
                    # Try to repair malformed JSON
                    logger.warning(
                        "structured_extraction_string_args_parse_failed",
                        preview=arguments[:200],
                    )
                    repaired_json, _was_truncated = self._repair_json(arguments)
                    try:
                        parsed_repaired = json.loads(repaired_json)
                        if isinstance(parsed_repaired, dict):
                            extracted_data = parsed_repaired.get("data", parsed_repaired)
                            logger.info(
                                "structured_extraction_repaired_string_args",
                                data_keys=list(extracted_data.keys())
                                if isinstance(extracted_data, dict)
                                else None,
                            )
                            return cast("dict[str, Any]", extracted_data)
                    except json.JSONDecodeError:
                        pass  # Repair failed, fall through

        # If repair failed or arguments were not a string, fall through to content parsing
        return self._extract_from_content(result)

    def _extract_from_content(self, result: dict[str, Any]) -> dict[str, Any]:
        """Extract structured data from response content (fallback).

        Args:
            result: LLM response

        Returns:
            Extracted structured data

        Raises:
            ValueError: If extraction fails

        """
        logger.warning(
            "structured_extraction_fallback_to_content_parsing", reason="no_tool_calls_found"
        )

        response_content = result.get("content", "")
        if isinstance(response_content, dict):
            response_content = response_content.get("content", "")

        if not response_content:
            error_msg = "LLM returned empty response. This may indicate token limit exhaustion or model failure."
            logger.exception(
                "structured_extraction_empty_response",
                reason="token_limit_or_model_failure",
                result_keys=list(result.keys()),
            )
            raise ValueError(error_msg)

        logger.debug(
            "structured_extraction_response_content",
            length=len(response_content),
            preview=response_content[:200],
        )

        # Try to parse JSON from response
        try:
            parsed_json = json.loads(response_content)
            extracted_data = (
                parsed_json.get("data", parsed_json)
                if isinstance(parsed_json, dict)
                else parsed_json
            )
            logger.info(
                "structured_extraction_json_parsed",
                data_keys=list(extracted_data.keys()) if isinstance(extracted_data, dict) else None,
            )
            return cast("dict[str, Any]", extracted_data)
        except json.JSONDecodeError as e:
            logger.warning(
                "structured_extraction_initial_parse_failed",
                error_type=map_exception_to_error_type(e),
                error_message=str(e),
            )

        # Attempt JSON repair
        repaired, was_truncated = self._repair_json(response_content)

        try:
            parsed_json = json.loads(repaired)
            logger.info("structured_extraction_repaired_successfully")
            return cast(
                "dict[str, Any]",
                parsed_json.get("data", parsed_json)
                if isinstance(parsed_json, dict)
                else parsed_json,
            )
        except json.JSONDecodeError as e2:
            # Standard repair failed - try item-level recovery for flat arrays
            # This extracts all complete items before the truncation point
            if was_truncated and '"items"' in response_content:
                items, recovered_count, _ = self._extract_complete_items_from_truncated(
                    response_content
                )
                if items:
                    logger.info(
                        "structured_extraction_item_level_recovery_success",
                        items_recovered=recovered_count,
                    )
                    return {"items": items}

            logger.exception(
                "structured_extraction_repair_failed",
                error_type=map_exception_to_error_type(e2),
                error_message=str(e2),
                response_length=len(response_content),
                response_preview=response_content[:500],
                response_ending=response_content[-500:],
            )

            error = ValueError(f"Response is not valid JSON even after repair: {e2}")
            error.was_truncated = was_truncated  # type: ignore[attr-defined]
            raise error from e2

    def _repair_json(self, text: str) -> tuple[str, bool]:
        """Attempt to repair malformed JSON with advanced fixes.

        Common issues:
        - Markdown code blocks
        - Missing commas
        - Truncation
        - Extra text before/after JSON
        - Incomplete strings at end

        Args:
            text: Potentially malformed JSON string

        Returns:
            Tuple of (repaired_json_string, was_truncated_flag)

        """
        # Remove markdown code blocks
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # Extract JSON portion
        text = self._extract_json_object(text)

        # Count braces to detect truncation
        open_braces = text.count("{")
        close_braces = text.count("}")
        open_brackets = text.count("[")
        close_brackets = text.count("]")

        is_truncated = open_braces > close_braces or open_brackets > close_brackets
        if is_truncated:
            logger.warning(
                "structured_extraction_truncation_detected",
                open_braces=open_braces,
                close_braces=close_braces,
                open_brackets=open_brackets,
                close_brackets=close_brackets,
            )

        # Fix unquoted string values (common with some models)
        # Pattern: "key": unquoted value, or "key": unquoted value}
        # Must not match: true, false, null, numbers, or already-quoted strings
        def quote_unquoted_values(match: re.Match) -> str:
            key = match.group(1)
            value = match.group(2).strip()
            terminator = match.group(3)
            # Skip if already looks like valid JSON value
            if value in ("true", "false", "null") or value.startswith('"'):
                return cast("str", match.group(0))
            # Skip if it's a number
            try:
                float(value)
                return cast("str", match.group(0))
            except ValueError:
                pass
            # Skip if it starts with { or [ (nested object/array)
            if value.startswith(("{", "[")):
                return cast("str", match.group(0))
            # Quote the value, escaping internal quotes
            escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{key}": "{escaped_value}"{terminator}'

        # Match "key": value, or "key": value} where value is unquoted
        text = re.sub(
            r'"([^"]+)":\s*([^,\[\]{}][^,}\]]*?)([,}\]])',
            quote_unquoted_values,
            text,
        )

        # Fix missing commas
        text = re.sub(r'"\s*\n\s*"', '",\n"', text)
        text = re.sub(r"}\s*\n\s*{", "},\n{", text)
        text = re.sub(r"]\s*\n\s*{", "],\n{", text)
        text = re.sub(r"}\s*\n\s*\[", "},\n[", text)
        text = re.sub(r'(["\d\]}])\s*\n\s*"([^"]+)"\s*:', r'\1,\n"\2":', text)

        # Remove trailing commas
        text = re.sub(r",(\s*[}\]])", r"\1", text)

        # Handle incomplete strings at the end
        if is_truncated:
            lines = text.split("\n")
            if lines:
                last_line = lines[-1]
                if last_line.count('"') % 2 == 1:
                    logger.info("structured_extraction_closing_incomplete_string")
                    text += '"'

                if re.search(r'"[^"]*":\s*[^,}\]]*$', last_line.strip()):
                    logger.info("structured_extraction_removing_incomplete_property")
                    text = "\n".join(lines[:-1])

        # Add missing closing braces/brackets
        if open_braces > close_braces:
            missing = open_braces - close_braces
            logger.info("structured_extraction_adding_closing_braces", count=missing)
            text += "}" * missing

        if open_brackets > close_brackets:
            missing = open_brackets - close_brackets
            logger.info("structured_extraction_adding_closing_brackets", count=missing)
            text += "]" * missing

        return text, is_truncated

    def _extract_json_object(self, text: str) -> str:
        """Extract the first complete JSON object or array from text.

        Args:
            text: Text potentially containing JSON

        Returns:
            Extracted JSON string

        """
        start_obj = text.find("{")
        start_arr = text.find("[")

        if start_obj == -1 and start_arr == -1:
            return text

        if start_obj == -1:
            start = start_arr
        elif start_arr == -1:
            start = start_obj
        else:
            start = min(start_obj, start_arr)

        # Find matching closing bracket
        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(text)):
            char = text[i]

            if escape:
                escape = False
                continue

            if char == "\\":
                escape = True
                continue

            if char == '"':
                in_string = not in_string
                continue

            if not in_string:
                if char in "{[":
                    depth += 1
                elif char in "}]":
                    depth -= 1
                    if depth == 0:
                        return text[start : i + 1]

        return text[start:]

    def _extract_complete_items_from_truncated(
        self,
        truncated_json: str,
    ) -> tuple[list[dict[str, Any]], int, bool]:
        """Extract all complete items from a truncated flat items array.

        For truncation resilience: even if the JSON is cut off mid-item,
        this method extracts all valid items that appeared before the
        truncation point.

        Args:
            truncated_json: Potentially truncated JSON string with items array

        Returns:
            Tuple of (valid_items, items_recovered, was_truncated)

        """
        items: list[dict[str, Any]] = []
        was_truncated = False

        # Clean up markdown if present
        text = truncated_json.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # Find the items array
        items_match = re.search(r'"items"\s*:\s*\[', text)
        if not items_match:
            # Try to find any array that might contain items
            array_start = text.find("[")
            if array_start == -1:
                return [], 0, False
            current_pos = array_start + 1
        else:
            current_pos = items_match.end()

        # Detect if truncation occurred (unbalanced braces/brackets)
        open_braces = text.count("{")
        close_braces = text.count("}")
        open_brackets = text.count("[")
        close_brackets = text.count("]")
        was_truncated = open_braces > close_braces or open_brackets > close_brackets

        # Parse items one by one
        while current_pos < len(text):
            # Skip whitespace and commas
            while current_pos < len(text) and text[current_pos] in " \t\n\r,":
                current_pos += 1

            if current_pos >= len(text):
                break

            # Check for end of array
            if text[current_pos] == "]":
                break

            # Find next object start
            if text[current_pos] != "{":
                # Not an object - skip to next potential item
                current_pos += 1
                continue

            obj_start = current_pos

            # Extract the JSON object starting at this position
            obj_text = self._extract_json_object(text[obj_start:])

            # If extracted text doesn't end with '}', the object was truncated
            if not obj_text.endswith("}"):
                was_truncated = True
                logger.info(
                    "structured_extraction_truncation_mid_item",
                    items_recovered=len(items),
                    truncation_position=obj_start,
                )
                break

            obj_end = obj_start + len(obj_text) - 1
            try:
                item = json.loads(obj_text)
                items.append(item)
                current_pos = obj_end + 1
            except json.JSONDecodeError as e:
                logger.debug(
                    "structured_extraction_item_parse_failed",
                    position=obj_start,
                    error=str(e),
                )
                # Skip this item and continue
                current_pos = obj_end + 1
                continue

        if was_truncated and items:
            logger.info(
                "structured_extraction_truncation_recovery",
                items_recovered=len(items),
            )

        return items, len(items), was_truncated
