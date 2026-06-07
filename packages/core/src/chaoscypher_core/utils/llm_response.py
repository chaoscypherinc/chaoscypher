# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM response parsing utilities.

Framework-agnostic helpers for extracting structured content from
provider-returned response dicts. Lives outside the adapter layer so
services can consume responses without importing LLM provider code.
"""

import json
import re
from typing import Any

import structlog


logger = structlog.get_logger(__name__)


def extract_content_with_fallback(
    response: dict[str, Any], expected_format: str = "text", fallback_message: str | None = None
) -> str:
    """Extract content from LLM response with intelligent fallback to thinking.

    Handles the "thinking-only" response pattern where the LLM generates
    extensive reasoning but no user-facing content.

    Args:
        response: LLM response dict with 'content' and optionally 'thinking'
        expected_format: 'text' or 'json' - affects extraction strategy
        fallback_message: Custom message if both content and thinking are empty

    Returns:
        Extracted content string (may be JSON or plain text)

    """
    logger.info(
        "extract_content_fallback_called",
        response_type=type(response).__name__,
        keys=list(response.keys()) if isinstance(response, dict) else None,
    )

    content = str(response.get("content", ""))
    thinking = str(response.get("thinking", ""))

    logger.info(
        "content_thinking_lengths",
        content_length=len(content),
        thinking_length=len(thinking),
    )

    if content and content.strip():
        return content

    if thinking and thinking.strip():
        logger.warning("content_empty_fallback_to_thinking", thinking_length=len(thinking))

        if expected_format == "json":
            extracted_json = _extract_json_from_text(thinking)
            if extracted_json:
                logger.info("json_extracted_from_thinking")
                return extracted_json

        return thinking

    error_msg = fallback_message or "LLM response was empty (no content or thinking generated)."
    logger.error("llm_response_empty", has_fallback=fallback_message is not None)
    return error_msg


def _extract_json_from_text(text: str) -> str | None:
    """Extract JSON from text that may contain markdown or other formatting.

    Tries multiple extraction strategies:
    1. Direct JSON parse
    2. Extract from ```json markdown blocks
    3. Extract from generic ``` code blocks
    4. Extract JSON arrays [...] or objects {...}

    Args:
        text: Text potentially containing JSON

    Returns:
        Extracted JSON string or None if extraction fails

    """
    try:
        parsed = json.loads(text)
        return json.dumps(parsed)
    except json.JSONDecodeError:
        pass

    if "```json" in text:
        try:
            json_str = text.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0].strip()
            json.loads(json_str)
            logger.info("json_extracted_from_markdown_block")
            return json_str
        except json.JSONDecodeError, IndexError:
            pass

    if "```" in text:
        try:
            json_str = text.split("```", maxsplit=1)[1].split("```", maxsplit=1)[0].strip()
            json.loads(json_str)
            logger.info("json_extracted_from_code_block")
            return json_str
        except json.JSONDecodeError, IndexError:
            pass

    if "[" in text:
        try:
            json_match = re.search(r"\[[\s\S]*\]", text)
            if json_match:
                json_str = json_match.group(0)
                json.loads(json_str)
                logger.info("json_array_extracted_regex")
                return json_str
        except json.JSONDecodeError, re.error:
            pass

    if "{" in text:
        try:
            json_match = re.search(r"\{[\s\S]*\}", text)
            if json_match:
                json_str = json_match.group(0)
                json.loads(json_str)
                logger.info("json_object_extracted_regex")
                return json_str
        except json.JSONDecodeError, re.error:
            pass

    logger.warning("json_extraction_failed", text_length=len(text))
    return None
