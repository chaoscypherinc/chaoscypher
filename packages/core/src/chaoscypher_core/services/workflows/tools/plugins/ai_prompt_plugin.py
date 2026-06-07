# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""AI Prompt Plugin - Direct LLM Interaction with Chunking Support.

Executes AI prompts with intelligent chunking for long documents:
1. Text chunking for long documents (quick/full strategies)
2. Parallel chunk processing via LLM queue
3. JSON extraction from responses (including thinking sections)
4. Result merging and formatting

Extracted from executors/llm_prompting.py and converted to plugin architecture.
"""

import asyncio
import json
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import OperationError


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.plugins import ToolExecutionContext

logger = structlog.get_logger(__name__)


# Instruction markers for splitting prompt content
INSTRUCTION_MARKERS = ["\n\nExtract", "\n\nIdentify", "\n\nReturn", "\n\nPlease"]


def chunk_text(text: str, max_tokens: int) -> list[str]:
    """Chunk text into segments for processing.

    Uses paragraph boundaries to split text. Approximates 4 chars ≈ 1 token.

    Args:
        text: Text to chunk
        max_tokens: Maximum tokens per chunk

    Returns:
        List of text chunks

    """
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return [text]

    chunks = []
    paragraphs = text.split("\n\n")
    current_chunk = ""

    for para in paragraphs:
        if len(current_chunk) + len(para) <= max_chars:
            current_chunk += para + "\n\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = para + "\n\n"

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks if chunks else [text]


def merge_json_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge JSON results from multiple chunks.

    Lists are extended, dicts are updated, scalars use first value.

    Args:
        results: List of result dictionaries

    Returns:
        Merged dictionary

    """
    if not results:
        return {}
    if len(results) == 1:
        return results[0]

    merged: dict[str, Any] = {}
    for result in results:
        for key, value in result.items():
            if isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            elif isinstance(value, dict):
                merged.setdefault(key, {}).update(value)
            else:
                merged.setdefault(key, value)

    return merged


class PromptPlugin:
    """AI Prompt tool plugin.

    Execute AI prompts with optional chunking for long content. Supports both
    direct prompting and chunked processing for large documents. When chunking
    is enabled, processes chunks in parallel via LLM queue and merges results
    intelligently based on output format.
    """

    @property
    def tool_id(self) -> str:
        """Stub implementation - not yet implemented."""
        return "ai.prompt"

    @property
    def category(self) -> str:
        """Stub implementation - not yet implemented."""
        return "ai"

    @property
    def icon(self) -> str:
        """MUI icon name for UI display."""
        return "SmartToy"

    @property
    def name(self) -> str:
        """Stub implementation - not yet implemented."""
        return "AI Prompt"

    @property
    def description(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Execute AI prompts with optional chunking for long content"

    @property
    def input_schema(self) -> dict[str, Any]:
        """Input schema for AI Prompt tool."""
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Main prompt text"},
                "system_prompt": {"type": "string", "description": "Optional system prompt"},
                "context": {"type": "string", "description": "Optional additional context"},
                "output_format": {
                    "type": "string",
                    "enum": ["text", "json"],
                    "description": "Output format (text or json)",
                    "default": "text",
                },
                "temperature": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 2.0,
                    "description": "LLM temperature (0.0-2.0, uses ai_temperature from settings if not specified)",
                },
                "max_tokens": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum tokens to generate",
                },
                "chunk_strategy": {
                    "type": "string",
                    "enum": ["none", "quick", "full"],
                    "description": "Chunking strategy for long documents",
                },
                "chunk_overlap": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Overlap between chunks (characters)",
                    "default": 500,
                },
                "thinking_mode": {
                    "type": "string",
                    "description": "Override thinking mode for this operation",
                },
                "file_id": {"type": "string", "description": "Optional file ID for metadata"},
            },
            "required": ["prompt"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        """Output schema for AI Prompt tool."""
        return {
            "type": "object",
            "properties": {
                "result": {
                    "type": "string",
                    "description": "Generated text response from the LLM",
                },
                "model": {
                    "type": "string",
                    "description": "Name of the model used for generation",
                },
                "tokens_used": {
                    "type": "integer",
                    "description": "Number of tokens consumed",
                },
                "_metadata": {
                    "type": "object",
                    "description": "Additional metadata about the execution",
                    "properties": {
                        "model": {"type": "string"},
                        "tokens_used": {"type": "integer"},
                        "chunks_processed": {"type": "integer"},
                        "chunk_strategy": {"type": "string"},
                    },
                },
            },
            "required": ["result"],
        }

    async def execute(
        self, inputs: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any]:
        """Execute AI prompt with optional chunking.

        Args:
            inputs: Tool inputs (validated against input_schema)
            context: Execution context with services

        Returns:
            Dictionary with result and metadata

        Raises:
            OperationError: If LLM service not available

        """
        if not context.llm_service:
            raise OperationError(
                "AI tools require LLM service",
                operation="ai.prompt",
            )

        # Extract inputs
        prompt = inputs["prompt"]
        system_prompt = inputs.get("system_prompt", "")
        input_context = inputs.get("context", "")
        output_format = inputs.get("output_format", "text")
        temperature: float = inputs.get("temperature") or (
            context.settings.llm.ai_temperature if context.settings is not None else 0.7
        )
        max_tokens: int = inputs.get("max_tokens") or (
            context.settings.llm.ai_max_tokens if context.settings is not None else 2048
        )
        tool_thinking_mode = inputs.get("thinking_mode", context.thinking_mode)
        chunk_strategy = inputs.get("chunk_strategy")
        chunk_overlap: int = inputs.get("chunk_overlap") or (
            context.settings.chunking.small_chunk_overlap if context.settings is not None else 50
        )

        # Build full prompt
        full_prompt = prompt
        if input_context:
            full_prompt = f"{input_context}\n\n{prompt}"

        # Check if chunking is needed
        if chunk_strategy and chunk_strategy != "none":
            return await self._execute_with_chunking(
                full_prompt,
                system_prompt,
                output_format,
                temperature,
                max_tokens,
                tool_thinking_mode,
                chunk_strategy,
                chunk_overlap,
                context.llm_service,
                inputs,
            )

        # No chunking - execute normally
        return await self._execute_single_prompt(
            full_prompt,
            system_prompt,
            output_format,
            temperature,
            max_tokens,
            tool_thinking_mode,
            context.llm_service,
        )

    async def _execute_with_chunking(
        self,
        full_prompt: str,
        system_prompt: str,
        output_format: str,
        temperature: float,
        max_tokens: int,
        tool_thinking_mode: str | None,
        chunk_strategy: str,
        chunk_overlap: int,
        llm_service: Any,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute prompt with text chunking for long documents.

        Args:
            full_prompt: Complete prompt text
            system_prompt: System prompt
            output_format: Output format (text/json)
            temperature: LLM temperature
            max_tokens: Max tokens per chunk
            tool_thinking_mode: Thinking mode override
            chunk_strategy: Chunking strategy
            chunk_overlap: Chunk overlap (unused currently)
            llm_service: LLM service instance
            inputs: Original inputs for metadata

        Returns:
            Merged results dictionary

        """
        # Extract content parts from prompt
        prompt_parts = self._extract_prompt_parts(full_prompt)
        if prompt_parts is None:
            # No content marker found, fall back to single execution
            return await self._execute_single_prompt(
                full_prompt,
                system_prompt,
                output_format,
                temperature,
                max_tokens,
                tool_thinking_mode,
                llm_service,
            )

        prompt_prefix, content_to_chunk, prompt_suffix = prompt_parts

        # Chunk the content
        chunks = chunk_text(content_to_chunk, max_tokens=max_tokens)
        logger.info("chunking_strategy_applied", strategy=chunk_strategy, chunk_count=len(chunks))

        # Process all chunks in parallel
        chunk_tasks = [
            self._process_single_chunk(
                i,
                chunk,
                prompt_prefix,
                prompt_suffix,
                system_prompt,
                output_format,
                temperature,
                max_tokens,
                chunk_strategy,
                llm_service,
                inputs,
                len(chunks),
            )
            for i, chunk in enumerate(chunks)
        ]

        logger.info("chunks_queued_for_processing", chunk_count=len(chunk_tasks))

        # Bound the number of in-flight chunk coroutines (each holds a
        # wait_for_result poller) so a very large operator-supplied prompt
        # doesn't materialize an unbounded number of concurrent pollers. The
        # LLM work itself is already serialized + spend-capped behind QUEUE_LLM.
        from chaoscypher_core.app_config import get_settings

        max_concurrent = max(1, get_settings().workers.operations_max_concurrent)
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _bounded(coro: Any) -> Any:
            """Await ``coro`` while holding a slot in the concurrency semaphore."""
            async with semaphore:
                return await coro

        # Wait for all chunks (concurrency capped by the semaphore)
        chunk_results_with_metadata = await asyncio.gather(
            *(_bounded(task) for task in chunk_tasks)
        )

        # Build final result
        return self._merge_chunk_results(
            chunk_results_with_metadata, output_format, chunk_strategy, len(chunks)
        )

    def _extract_prompt_parts(self, full_prompt: str) -> tuple[str, str, str] | None:
        """Extract content portion from prompt for chunking.

        Args:
            full_prompt: Full prompt text

        Returns:
            Tuple of (prefix, content, suffix) or None if no content marker

        """
        content_marker = "Content:\n"
        content_start = full_prompt.find(content_marker)

        if content_start == -1:
            return None

        content_start += len(content_marker)

        # Find where instructions begin
        content_end = len(full_prompt)
        for marker in INSTRUCTION_MARKERS:
            marker_pos = full_prompt.find(marker, content_start)
            if marker_pos != -1:
                content_end = min(content_end, marker_pos)

        return (
            full_prompt[:content_start],
            full_prompt[content_start:content_end],
            full_prompt[content_end:],
        )

    async def _process_single_chunk(
        self,
        chunk_index: int,
        chunk: str,
        prompt_prefix: str,
        prompt_suffix: str,
        system_prompt: str,
        output_format: str,
        temperature: float,
        max_tokens: int,
        chunk_strategy: str,
        llm_service: Any,
        inputs: dict[str, Any],
        total_chunks: int,
    ) -> tuple[int, dict[str, Any], str]:
        """Process a single chunk and return parsed result.

        Args:
            chunk_index: Index of this chunk
            chunk: Chunk content
            prompt_prefix: Prompt prefix before content
            prompt_suffix: Prompt suffix after content
            system_prompt: System prompt
            output_format: Output format
            temperature: LLM temperature
            max_tokens: Max tokens
            chunk_strategy: Strategy name for metadata
            llm_service: LLM service
            inputs: Original inputs for metadata
            total_chunks: Total number of chunks

        Returns:
            Tuple of (index, result_dict, model_name)

        """
        logger.info(
            "processing_chunk",
            chunk_index=chunk_index + 1,
            total_chunks=total_chunks,
            chunk_length=len(chunk),
        )

        # Rebuild prompt with this chunk
        chunk_prompt = f"{prompt_prefix}{chunk}{prompt_suffix}"
        if output_format == "json":
            chunk_prompt += "\n\nPlease respond with valid JSON only."

        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": chunk_prompt})

        # Queue LLM task
        task_id = await llm_service.queue_operation(
            task_type="chat",
            operation_name="chat_completion",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            metadata={
                "tool": "ai.prompt",
                "output_format": output_format,
                "chunk": f"{chunk_index + 1}/{total_chunks}",
                "strategy": chunk_strategy,
                "file_id": inputs.get("file_id"),
            },
        )

        # Wait for result
        min_timeout = llm_service.settings.timeouts.llm_chat_wait
        calculated_timeout = max_tokens // 10 + 60
        chunk_result = await llm_service.wait_for_result(
            task_id, timeout=max(calculated_timeout, min_timeout)
        )

        # Parse output
        output = self._parse_llm_output(chunk_result, output_format)

        return (chunk_index, output, chunk_result.get("model", "unknown"))

    def _merge_chunk_results(
        self,
        chunk_results_with_metadata: list[tuple[int, Any, str]],
        output_format: str,
        chunk_strategy: str,
        chunk_count: int,
    ) -> dict[str, Any]:
        """Merge results from all chunks.

        Args:
            chunk_results_with_metadata: List of (index, result, model) tuples
            output_format: Output format
            chunk_strategy: Strategy name
            chunk_count: Number of chunks

        Returns:
            Merged result dictionary

        """
        # Sort by index
        chunk_results_with_metadata.sort(key=lambda x: x[0])
        chunk_results = [result[1] for result in chunk_results_with_metadata]
        last_model = (
            chunk_results_with_metadata[-1][2] if chunk_results_with_metadata else "unknown"
        )

        # Merge results
        merged_output: dict[str, Any] | str
        if output_format == "json":
            merged_output = merge_json_results(chunk_results)
            logger.info(
                "chunk_results_merged",
                entity_count=len(merged_output.get("entities", [])),
                summary_length=len(merged_output.get("summary", "")),
            )
        else:
            merged_output = "\n\n---\n\n".join(str(r) for r in chunk_results)

        # Return merged result
        if output_format == "json" and isinstance(merged_output, dict):
            return {
                **merged_output,
                "_metadata": {
                    "model": last_model,
                    "chunks_processed": chunk_count,
                    "chunk_strategy": chunk_strategy,
                },
            }

        return {
            "result": merged_output,
            "_metadata": {
                "model": last_model,
                "chunks_processed": chunk_count,
                "chunk_strategy": chunk_strategy,
            },
        }

    async def _execute_single_prompt(
        self,
        full_prompt: str,
        system_prompt: str,
        output_format: str,
        temperature: float,
        max_tokens: int,
        tool_thinking_mode: str | None,
        llm_service: Any,
    ) -> dict[str, Any]:
        """Execute single prompt without chunking."""
        # Format for JSON if needed
        if output_format == "json":
            full_prompt += "\n\nPlease respond with valid JSON only."

        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": full_prompt})

        # Queue task
        task_id = await llm_service.queue_operation(
            task_type="chat",
            operation_name="chat_completion",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            metadata={"tool": "ai.prompt", "output_format": output_format},
        )

        # Wait for result
        min_timeout = llm_service.settings.timeouts.llm_chat_wait
        calculated_timeout = max_tokens // 10 + 60
        result = await llm_service.wait_for_result(
            task_id, timeout=max(calculated_timeout, min_timeout)
        )

        # Parse output
        output = self._parse_llm_output(result, output_format)

        # Format response
        if output_format == "json" and isinstance(output, dict):
            return {
                **output,
                "_metadata": {
                    "model": result.get("model", "unknown"),
                    "tokens_used": result.get("tokens_used", 0),
                },
            }
        return {
            "result": output,
            "model": result.get("model", "unknown"),
            "tokens_used": result.get("tokens_used", 0),
        }

    def _parse_llm_output(self, result: dict[str, Any], output_format: str) -> Any:
        """Parse LLM output, extracting JSON if needed (including from thinking section)."""
        output = result.get("content", "")

        if output_format != "json":
            return output

        # Try parsing JSON
        if isinstance(output, str):
            output = self._extract_json_from_text(output)

        # If output empty/incomplete, check thinking section
        if isinstance(output, dict) and (not output or not output.get("entities")):
            thinking = result.get("thinking", "")
            if thinking:
                logger.info(
                    "extracting_json_from_thinking_section",
                    reason="response_entities_empty",
                    thinking_length=len(thinking),
                )
                thinking_output = self._extract_json_from_text(thinking)
                if isinstance(thinking_output, dict) and thinking_output.get("entities"):
                    logger.info(
                        "extracted_entities_from_thinking",
                        entity_count=len(thinking_output.get("entities", [])),
                    )
                    return thinking_output

        return output

    def _extract_json_from_text(self, text: str) -> Any:
        """Extract JSON from text, handling markdown code blocks."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try extracting from markdown code blocks
            for delimiter in ("```json", "```"):
                if delimiter in text:
                    parts = text.split(delimiter, maxsplit=1)
                    if len(parts) > 1:
                        inner = parts[1].split("```", maxsplit=1)[0].strip()
                        if inner:
                            return json.loads(inner)
        except Exception:
            logger.debug("json_code_block_extraction_failed")
        return text


__all__ = ["PromptPlugin"]
