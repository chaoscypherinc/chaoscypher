# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Anthropic LLM Provider using LangChain.

Provider for Anthropic Claude models (Claude 3.5 Sonnet, Claude 3 Opus, etc.).
Supports chat completion, streaming, and tool calling.
"""

from typing import TYPE_CHECKING, Any, ClassVar

import structlog
from langchain_anthropic import ChatAnthropic

from chaoscypher_core.adapters.llm.providers.base import (
    BaseLLMProvider,
    extract_streaming_finish_reason,
    extract_streaming_usage,
    normalize_finish_reason,
)
from chaoscypher_core.adapters.llm.providers.error_classifier import (
    ProviderErrorPatterns,
    classify_provider_error,
)
from chaoscypher_core.adapters.llm.utils import (
    convert_to_langchain_messages,
    format_tool_calls_response,
)
from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.plugins.base import PluginMetadata


if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

logger = structlog.get_logger(__name__)

# Anthropic-specific error indicator patterns
_PATTERNS = ProviderErrorPatterns(
    provider="anthropic",
    rate_limit=("429", "rate limit", "too many requests", "overloaded"),
    auth=(
        "401",
        "403",
        "api key",
        "invalid key",
        "authentication",
        "unauthorized",
        "invalid_api_key",
    ),
    model_not_found=("404", "not found", "invalid model", "unknown model"),
    content_filter=("safety", "harmful", "blocked", "flagged"),
    content_filter_type="SAFETY",
    context_length=("context_length", "context length", "token limit", "too long"),
    server_error=(
        "500",
        "502",
        "503",
        "504",
        "529",
        "internal",
        "server error",
        "service unavailable",
        "overloaded",
    ),
)


def _classify_anthropic_error(error: Exception, model: str) -> LLMError:
    """Classify an Anthropic API error into a specific LLMError type.

    Args:
        error: The original exception from Anthropic/LangChain.
        model: The model that was being used.

    Returns:
        An appropriate LLMError subclass with user-friendly messaging.

    """
    return classify_provider_error(error, model, _PATTERNS)


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude provider using LangChain.

    Supports:
    - Claude 3.5 Sonnet, Claude 3 Opus, Claude 3 Sonnet, Claude 3 Haiku
    - Streaming and non-streaming chat
    - Tool calling
    - Extended context (200k tokens)
    """

    _METADATA: ClassVar[PluginMetadata] = PluginMetadata(
        plugin_id="anthropic",
        name="Anthropic",
        description="Anthropic Claude models (LangChain).",
        version="1.0.0",
        author="Chaos Cypher, Inc.",
        category="llm_provider",
        builtin=True,
        tags=["streaming", "tool_calling", "vision"],
        origin="builtin",
    )

    @property
    def metadata(self) -> PluginMetadata:
        """Return the plugin metadata declared on the class."""
        return self._METADATA

    def __init__(self, config: dict[str, Any]):
        """Initialize the instance.

        Args:
            config: Provider configuration containing API keys and model settings.

        """
        super().__init__(config)
        self.api_key = config.get("anthropic_api_key")
        self.chat_model = config["anthropic_chat_model"]

        # Initialize LangChain model
        self.llm = self._init_llm()

    def _init_llm(self) -> BaseChatModel:
        """Initialize LangChain ChatAnthropic."""
        kwargs = {
            "model": self.chat_model,
            "anthropic_api_key": self.api_key,
        }

        # Add generic LLM settings
        if self.config.get("ai_temperature") is not None:
            kwargs["temperature"] = self.config.get("ai_temperature")
        if self.config.get("ai_max_tokens") is not None:
            kwargs["max_tokens"] = self.config.get("ai_max_tokens")

        # Bounded request timeout — LangChain forwards to the httpx client.
        timeout = self.config.get("llm_request_timeout")
        if timeout is not None:
            kwargs["timeout"] = timeout

        logger.info("anthropic_initialized", provider="anthropic", model=self.chat_model)
        return ChatAnthropic(**kwargs)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        stream: bool = False,
        enable_thinking: bool = False,
        high_priority: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Send chat request to Anthropic using LangChain."""
        # Convert messages to LangChain format
        lc_messages = convert_to_langchain_messages(messages)

        from typing import cast

        try:
            if stream:
                # Streaming mode
                return cast(
                    "dict[str, Any]",
                    self._wrap_stream_with_semaphore(
                        self._stream_chat(lc_messages, tools, temperature, max_tokens),
                        high_priority=high_priority,
                    ),
                )
            # Non-streaming mode with semaphore
            if self.enable_priority:
                logger.debug(
                    "semaphore_requested", provider="anthropic", high_priority=high_priority
                )
                async with self.semaphore.acquire(high_priority=high_priority):
                    logger.debug(
                        "semaphore_acquired", provider="anthropic", high_priority=high_priority
                    )
                    logger.debug(
                        "anthropic_request_sent",
                        provider="anthropic",
                        model=self.chat_model,
                        messages=messages,
                        tools=bool(tools),
                    )
                    result = await self._make_sync_request(
                        lc_messages, tools, temperature, max_tokens
                    )
                    logger.debug(
                        "anthropic_response_received",
                        provider="anthropic",
                        model=self.chat_model,
                        content_length=len(result.get("content", "")),
                        has_tool_calls=bool(result.get("tool_calls")),
                    )
            else:
                logger.debug(
                    "anthropic_request_sent",
                    provider="anthropic",
                    model=self.chat_model,
                    messages=messages,
                    tools=bool(tools),
                    priority_disabled=True,
                )
                result = await self._make_sync_request(lc_messages, tools, temperature, max_tokens)
                logger.debug(
                    "anthropic_response_received",
                    provider="anthropic",
                    model=self.chat_model,
                    content_length=len(result.get("content", "")),
                    has_tool_calls=bool(result.get("tool_calls")),
                )

            return result

        except LLMError:
            # Re-raise our own errors as-is
            raise
        except Exception as e:
            logger.exception(
                "anthropic_chat_failed",
                provider="anthropic",
                model=self.chat_model,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise _classify_anthropic_error(e, self.chat_model) from e

    async def _make_sync_request(
        self,
        lc_messages: list,
        tools: list[dict] | None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Make synchronous (non-streaming) request using LangChain."""
        # Start with base LLM
        llm_with_config = self.llm

        # Bind dynamic parameters if provided
        bind_kwargs = {}
        if temperature is not None:
            bind_kwargs["temperature"] = temperature
        if max_tokens is not None:
            bind_kwargs["max_tokens"] = max_tokens

        if bind_kwargs:
            llm_with_config = llm_with_config.bind(**bind_kwargs)

        # Bind tools if provided
        llm_with_tools = llm_with_config.bind_tools(tools) if tools else llm_with_config

        # Invoke LangChain model
        response = await llm_with_tools.ainvoke(lc_messages)

        # Extract tool calls if present
        tool_calls = None
        if hasattr(response, "tool_calls") and response.tool_calls:
            tool_calls = format_tool_calls_response(response.tool_calls)

        # Extract usage info
        usage = {}
        raw_finish_reason: str | None = None
        if hasattr(response, "usage_metadata"):
            usage_metadata = response.usage_metadata
            usage = {
                "prompt_tokens": usage_metadata.get("input_tokens", 0),
                "completion_tokens": usage_metadata.get("output_tokens", 0),
                "total_tokens": usage_metadata.get("input_tokens", 0)
                + usage_metadata.get("output_tokens", 0),
            }

        # Anthropic surfaces stop_reason in response_metadata (e.g.
        # "end_turn", "max_tokens", "tool_use", "stop_sequence").
        if hasattr(response, "response_metadata"):
            raw_finish_reason = response.response_metadata.get("stop_reason")

        return {
            "content": response.content,
            "thinking": None,  # Claude doesn't have separate thinking mode
            "tool_calls": tool_calls,
            "model": self.chat_model,
            "provider": "anthropic",
            "usage": usage,
            "finish_reason": normalize_finish_reason(raw_finish_reason),
        }

    async def _stream_chat(
        self,
        lc_messages: list,
        tools: list[dict] | None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """Stream chat responses using LangChain."""
        # Start with base LLM
        llm_with_config = self.llm

        # Bind dynamic parameters if provided
        bind_kwargs = {}
        if temperature is not None:
            bind_kwargs["temperature"] = temperature
        if max_tokens is not None:
            bind_kwargs["max_tokens"] = max_tokens

        if bind_kwargs:
            llm_with_config = llm_with_config.bind(**bind_kwargs)

        # Bind tools if provided
        llm_with_tools = llm_with_config.bind_tools(tools) if tools else llm_with_config

        accumulated_content = ""
        tool_calls = None
        usage: dict[str, int] = {}
        last_chunk = None
        raw_finish_reason: str | None = None

        try:
            # Use LangChain's async streaming
            async for chunk in llm_with_tools.astream(lc_messages):
                last_chunk = chunk

                # Extract content delta
                delta_content = chunk.content if hasattr(chunk, "content") else ""

                if delta_content:
                    accumulated_content += delta_content

                    yield {
                        "type": "content",
                        "delta": delta_content,
                        "accumulated": accumulated_content,
                    }

                # Check for tool calls in chunk
                if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                    tool_calls = format_tool_calls_response(chunk.tool_calls)

                # Anthropic surfaces ``stop_reason`` on the
                # message_stop event (LangChain stashes it in
                # response_metadata). Capture as we go.
                chunk_finish = extract_streaming_finish_reason(chunk)
                if chunk_finish:
                    raw_finish_reason = chunk_finish

            # Extract usage from last streaming chunk's metadata
            usage = extract_streaming_usage(last_chunk) or usage

            # Yield final chunk
            yield {
                "type": "done",
                "content": accumulated_content,
                "thinking": None,
                "tool_calls": tool_calls,
                "usage": usage,
                "model": self.chat_model,
                "provider": "anthropic",
                "finish_reason": normalize_finish_reason(raw_finish_reason),
            }

        except LLMError as e:
            logger.exception(
                "anthropic_streaming_failed",
                provider="anthropic",
                model=self.chat_model,
                error_type=type(e).__name__,
                error_message=str(e),
                error_code=e.code,
            )
            yield {
                "type": "error",
                "error": e.message,
                "error_code": e.code,
                "error_details": e.details,
            }
        except Exception as e:
            logger.exception(
                "anthropic_streaming_failed",
                provider="anthropic",
                model=self.chat_model,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            classified = _classify_anthropic_error(e, self.chat_model)
            yield {
                "type": "error",
                "error": classified.message,
                "error_code": classified.code,
                "error_details": classified.details,
            }
