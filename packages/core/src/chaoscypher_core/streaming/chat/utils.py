# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Streaming Chat Utilities.

Shared utility functions used across the streaming chat sub-package.
Provides SSE event formatting, thinking tag removal, LLM provider
setup, fallback response generation, and tool argument parsing.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, cast

import structlog

from chaoscypher_core.llm_queue.factory import get_provider_factory
from chaoscypher_core.llm_queue.provider_utils import get_provider_config
from chaoscypher_core.llm_queue.queue_factory import get_llm_queue_service
from chaoscypher_core.services.chat.engine.constants import (
    ESSENTIAL_TOOL_NAMES,
    MAX_TOOLS,
)
from chaoscypher_core.services.workflows.tools.engine.executor import ToolExecutorService
from chaoscypher_core.services.workflows.tools.engine.schema_registry import (
    get_essential_tool_schemas,
)


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings

logger = structlog.get_logger(__name__)


def extract_thinking_from_tags(content: str) -> str | None:
    """Extract thinking text from <think>...</think> tags in LLM content.

    Args:
        content: Raw content from LLM (may contain <think> tags)

    Returns:
        Extracted thinking text (joined if multiple blocks), or None if no tags found.

    """
    if not content:
        return None
    matches = re.findall(r"<think>([\s\S]*?)</think>", content, flags=re.IGNORECASE)
    if not matches:
        return None
    thinking = "\n\n".join(m.strip() for m in matches if m.strip())
    return thinking or None


def strip_thinking_tags(content: str) -> str:
    """Remove <think>...</think> tags from LLM content.

    Args:
        content: Raw content from LLM (may contain <think> tags)

    Returns:
        Content with thinking tags removed and whitespace normalized

    """
    if not content:
        return content
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE)
    cleaned = re.sub(r"</think>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def format_sse_event(event_type: str, data: dict[str, Any]) -> bytes:
    """Format data as SSE event.

    Args:
        event_type: Event type to include in data
        data: Event data dictionary

    Returns:
        SSE-formatted bytes

    """
    data["type"] = event_type
    return f"data: {json.dumps(data)}\n\n".encode()


def get_context_window_for_provider(settings: Settings) -> tuple[int, str, str]:
    """Get context window size for the current LLM provider.

    Args:
        settings: Application settings with LLM configuration

    Returns:
        Tuple of (context_window_tokens, provider_name, model_name)

    """
    config = get_provider_config(settings)
    return config.context_window, config.provider, config.model


def get_model_name(settings: Settings) -> str:
    """Get the model name based on provider settings.

    Args:
        settings: Application settings

    Returns:
        Model name string

    """
    return get_provider_config(settings).model


def setup_chat_providers(
    settings: Settings,
    graph_manager: Any,
    search_manager: Any,
    chat_id: str,
    indexing_manager: Any | None = None,
    source_ids: list[str] | None = None,
    source_storage: Any | None = None,
    llm_chat_callback_override: Any | None = None,
) -> tuple[Any, ToolExecutorService, list[dict[str, Any]]]:
    """Initialize LLM provider and tool executor.

    Args:
        settings: Application settings
        graph_manager: GraphRepository instance
        search_manager: SearchRepository instance
        chat_id: Chat ID for logging
        indexing_manager: Optional IndexingProtocol for chunk operations
        source_ids: Optional source IDs for scope filtering
        source_storage: Optional SourceStorageProtocol for citation lookups
        llm_chat_callback_override: Optional direct LLM callback that bypasses
            the queue. Required when running inside the Neuron worker (LLM queue
            concurrency=1 means queue-based callbacks deadlock).

    Returns:
        Tuple of (chat_provider, tool_executor, available_tools)

    """
    # Get provider name
    if hasattr(settings, "llm"):
        logger.info(
            "chat_stream_llm_config",
            chat_id=chat_id,
            llm_config_type=str(type(settings.llm)),
            ollama_url=settings.llm.primary_ollama_url,
        )
        provider_name = settings.llm.chat_provider.lower()
    else:
        logger.exception("chat_stream_settings_missing_llm", chat_id=chat_id)
        provider_name = "ollama"

    logger.debug(
        "chat_stream_initializing_provider",
        chat_id=chat_id,
        provider_name=provider_name,
    )

    # Create LLM provider (use singleton factory for connection pooling)
    factory = get_provider_factory()
    chat_provider = factory.get_chat_provider()

    # Get engine search settings for reranking config
    from chaoscypher_core.app_config.engine_factory import build_engine_settings

    engine_settings = build_engine_settings(settings)

    # Create queue-based callbacks for tool handlers (interactive priority)
    llm_queue = get_llm_queue_service()
    interactive_priority = settings.priorities.interactive

    from chaoscypher_core.repo_factories import get_embedding_service

    _embedding_service = get_embedding_service()

    async def embedding_callback(text: str) -> Any:
        """Local embedding via EmbeddingService."""
        return await _embedding_service.embed(text)

    if llm_chat_callback_override is not None:
        _llm_chat_callback = llm_chat_callback_override
    else:

        async def _llm_chat_callback(
            messages: list[dict[str, Any]],
            temperature: float | None = None,
            max_tokens: int | None = None,
        ) -> dict[str, Any]:
            """Queue-based LLM chat with interactive priority."""
            from chaoscypher_core.ports.llm import TaskType

            task_id = await llm_queue.queue_operation(
                task_type=TaskType.CHAT,
                operation_name="chat_completion",
                messages=messages,
                priority=interactive_priority,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return cast("dict[str, Any]", await llm_queue.wait_for_result(task_id))

    # Initialize tool executor with optional source scope
    scope = {"source_ids": source_ids} if source_ids else None

    tool_executor = ToolExecutorService(
        graph_repository=graph_manager,
        search_repository=search_manager,
        indexing_repository=indexing_manager,
        embedding_callback=embedding_callback,
        llm_chat_callback=_llm_chat_callback,
        research_agent_callback=None,
        search_settings=engine_settings.search,
        engine_settings=engine_settings,
        scope=scope,
        source_storage=source_storage,
    )

    # Get tool schemas from registry for essential tools
    all_tools = get_essential_tool_schemas(ESSENTIAL_TOOL_NAMES)
    available_tools = select_tools(all_tools, chat_id)

    logger.info(
        "chat_stream_tools_loaded",
        chat_id=chat_id,
        essential_tools=len(ESSENTIAL_TOOL_NAMES),
        available_tools=len(available_tools),
    )

    return chat_provider, tool_executor, available_tools


def select_tools(all_tools: list[dict[str, Any]], chat_id: str) -> list[dict[str, Any]]:
    """Select tools for chat, prioritizing essential ones.

    Args:
        all_tools: All available tools
        chat_id: Chat ID for logging

    Returns:
        List of selected tools (up to MAX_TOOLS)

    """
    available_tools = []

    # Add essential tools first
    for tool in all_tools:
        tool_name = tool.get("function", {}).get("name")
        if tool_name in ESSENTIAL_TOOL_NAMES:
            available_tools.append(tool)

    # Add remaining tools until limit
    for tool in all_tools:
        if len(available_tools) >= MAX_TOOLS:
            break
        if tool not in available_tools:
            available_tools.append(tool)

    logger.debug(
        "chat_stream_tools_selected",
        chat_id=chat_id,
        available_tools=len(available_tools),
        total_tools=len(all_tools),
    )

    return available_tools


def parse_tool_arguments(
    arguments_raw: str | dict[str, Any],
    tool_name: str,
    chat_id: str,
) -> dict[str, Any]:
    """Parse tool call arguments from string or dict.

    Args:
        arguments_raw: Raw arguments (string JSON or dict)
        tool_name: Tool name for logging
        chat_id: Chat ID for logging

    Returns:
        Parsed arguments dictionary

    """
    if isinstance(arguments_raw, str):
        try:
            parsed: dict[str, Any] = json.loads(arguments_raw)
            return parsed
        except json.JSONDecodeError:
            logger.warning(
                "chat_stream_tool_arguments_parse_failed",
                chat_id=chat_id,
                tool_name=tool_name,
                arguments_raw=str(arguments_raw)[:200],
            )
            return {}
    # isinstance(arguments_raw, dict) is always True here (union[str, dict] with str handled above)
    return arguments_raw


def create_fallback_response(
    has_thinking: bool,
    after_tools: bool = False,
    tools_were_available: bool = True,
) -> str:
    """Create a fallback response for empty LLM responses.

    Args:
        has_thinking: Whether thinking content exists
        after_tools: Whether this is after tool execution
        tools_were_available: Whether tools were passed to the LLM (helps diagnose issues)

    Returns:
        Fallback response string

    """
    if after_tools:
        return (
            "I've executed the tool(s) above. Please check the tool output for details "
            "on what was completed. If you need additional operations or have follow-up "
            "questions, feel free to ask."
        )

    if has_thinking:
        return (
            "I've thought about your question. Could you please rephrase or provide "
            "more details so I can better assist you?"
        )

    # If tools were available but we got no response, the model may not support tool calling
    if tools_were_available:
        return (
            "I wasn't able to generate a response. This model may not support tool calling, "
            "which is needed to search the knowledge graph.\n\n"
            "**Recommended models with tool calling support:**\n"
            "- qwen2.5:7b or qwen2.5:14b (excellent)\n"
            "- llama3.1:8b (good)\n"
            "- mistral:7b (basic)\n\n"
            "You can change your model in Settings > LLM Configuration."
        )

    return (
        "I apologize, but I didn't generate a response. Could you please try "
        "rephrasing your question?"
    )


__all__ = [
    "create_fallback_response",
    "format_sse_event",
    "get_context_window_for_provider",
    "get_model_name",
    "parse_tool_arguments",
    "select_tools",
    "setup_chat_providers",
    "strip_thinking_tags",
]
