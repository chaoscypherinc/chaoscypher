# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Ollama LLM Provider using LangChain.

Local LLM provider for Ollama models (e.g., Llama, Qwen, Mistral).
Supports chat completion, streaming, and thinking mode.
"""

import asyncio
import re
from typing import TYPE_CHECKING, Any, ClassVar, cast

import httpx
import structlog
from langchain_ollama import ChatOllama
from ollama._types import ResponseError

from chaoscypher_core.adapters.llm.providers.base import (
    BaseLLMProvider,
    extract_provider_timings,
    extract_streaming_finish_reason,
    extract_streaming_usage,
    normalize_finish_reason,
)
from chaoscypher_core.adapters.llm.utils import (
    convert_to_langchain_messages,
    format_tool_calls_response,
)
from chaoscypher_core.exceptions import LLMError, ToolCallingNotSupportedError
from chaoscypher_core.plugins.base import PluginMetadata


# Pattern to match <think>...</think> tags (including newlines)
_THINK_TAG_PATTERN = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
# Pattern to extract content from <think>...</think> tags
_THINK_TAG_EXTRACT_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)


if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

logger = structlog.get_logger(__name__)


class OllamaProvider(BaseLLMProvider):
    """Ollama local LLM provider using LangChain.

    Supports:
    - Local Ollama models (Llama, Qwen, Mistral, etc.)
    - Streaming and non-streaming chat
    - Tool calling
    - Thinking mode (Qwen3)
    """

    _METADATA: ClassVar[PluginMetadata] = PluginMetadata(
        plugin_id="ollama",
        name="Ollama",
        description="Local Ollama server (LangChain).",
        version="1.0.0",
        author="Chaos Cypher, Inc.",
        category="llm_provider",
        builtin=True,
        tags=["streaming", "tool_calling", "embeddings", "thinking"],
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
        self.stream_chunk_timeout: float = float(config["stream_chunk_timeout"])
        self.base_url = config["base_url"]
        self.chat_model = config["ollama_chat_model"]

        # Store defaults for instance creation
        self._default_temperature = config.get("ai_temperature")
        self._default_max_tokens = config.get("ai_max_tokens")
        self._health_check_timeout: float = float(config["ollama_health_check_timeout"])
        self._recovery_delay: float = float(config["ollama_recovery_delay"])

        # Cache for ChatOllama instances: (temperature, max_tokens, reasoning) -> instance
        self._llm_cache: dict[tuple[float | None, int | None, bool], BaseChatModel] = {}

        # Cached httpx client for health-check requests; lazy-initialised on
        # first call to check_health() so no event loop is needed at __init__
        # time. Timeout is sourced from settings (ollama_health_check) via
        # config["ollama_health_check_timeout"] which is already stored in
        # self._health_check_timeout.
        self._health_client: httpx.AsyncClient | None = None

        # Track whether the current model supports reasoning/thinking mode.
        # Flips to False after the first "does not support thinking" error,
        # so subsequent calls skip the wasteful try→fail→retry cycle.
        self._model_supports_reasoning: bool = True

        # Initialize LangChain model
        self.llm = self._init_llm()

    async def check_health(self) -> bool:
        """Check if Ollama is responsive.

        Makes a lightweight request to the Ollama API to verify it's running
        and accepting requests. Used before retrying failed requests to avoid
        wasting tokens on immediate retries when Ollama is overloaded.

        Returns:
            True if Ollama is healthy and responsive, False otherwise.

        """
        try:
            if self._health_client is None:
                self._health_client = httpx.AsyncClient(
                    timeout=self._health_check_timeout,
                )
            response = await self._health_client.get(f"{self.base_url}/api/tags")
            is_healthy = response.status_code == 200
            if not is_healthy:
                logger.warning(
                    "ollama_health_check_failed",
                    status_code=response.status_code,
                    base_url=self.base_url,
                )
            return is_healthy
        except Exception as e:
            logger.warning(
                "ollama_health_check_error",
                error_type=type(e).__name__,
                error_message=str(e),
                base_url=self.base_url,
            )
            return False

    def _init_llm(
        self,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning: bool = True,
    ) -> BaseChatModel:
        """Initialize LangChain ChatOllama.

        Args:
            temperature: Optional temperature override. Uses default if None.
            max_tokens: Optional max_tokens override. Uses default if None.
            reasoning: Whether to enable reasoning/thinking mode. Defaults to True
                for thinking-capable models (e.g., Qwen3). Set to False for models
                that don't support thinking (e.g., Llama, Mistral instruct models).

        Returns:
            Configured ChatOllama instance.

        """
        # Use provided params or fall back to defaults
        temp = temperature if temperature is not None else self._default_temperature
        tokens = max_tokens if max_tokens is not None else self._default_max_tokens

        # Base kwargs
        kwargs = {
            "model": self.chat_model,
            "base_url": self.base_url,
        }

        if temp is not None:
            kwargs["temperature"] = temp
        if tokens is not None:
            kwargs["num_predict"] = tokens

        # Ollama-specific performance options
        # These can be passed to improve GPU utilization
        if self.config.get("ollama_num_batch"):
            kwargs["num_batch"] = self.config.get("ollama_num_batch")
        if self.config.get("ollama_num_ctx"):
            kwargs["num_ctx"] = self.config.get("ollama_num_ctx")
        if self.config.get("ollama_num_parallel"):
            kwargs["num_parallel"] = self.config.get("ollama_num_parallel")
        if self.config.get("ollama_num_thread"):
            kwargs["num_thread"] = self.config.get("ollama_num_thread")

        # Always pass `reasoning` explicitly. LangChain's ChatOllama defaults to None,
        # which maps to `think=null` on the Ollama API and means "use the model default" —
        # so thinking-capable models (Qwen3, deepseek-r1, …) keep thinking even when the
        # caller asked us to disable it. Passing reasoning=False sends `think=false` and
        # actually disables thinking. reasoning=True enables it via additional_kwargs.
        # Non-thinking models (Llama, Mistral instruct) raise on reasoning=True; the
        # caller falls back via _invoke_with_thinking_fallback in that case.
        kwargs["reasoning"] = reasoning

        logger.debug(
            "ollama_initialized",
            provider="ollama",
            model=self.chat_model,
            base_url=self.base_url,
            temperature=temp,
            max_tokens=tokens,
        )
        return ChatOllama(**kwargs)

    def _get_cached_llm(
        self,
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        reasoning: bool = True,
    ) -> BaseChatModel:
        """Get or create a cached ChatOllama instance for the given parameters.

        Ollama 0.6.x doesn't support dynamic temperature/max_tokens via bind(),
        so we cache separate ChatOllama instances for different parameter combinations.

        Args:
            temperature: Optional temperature override.
            max_tokens: Optional max_tokens override.
            reasoning: Whether to enable reasoning/thinking mode.

        Returns:
            ChatOllama instance configured with the requested parameters.

        """
        # No overrides + default reasoning = use default instance
        if temperature is None and max_tokens is None and reasoning:
            if self.llm is None:
                raise RuntimeError("LLM not initialized")
            return self.llm

        cache_key = (temperature, max_tokens, reasoning)
        if cache_key in self._llm_cache:
            return self._llm_cache[cache_key]

        # Create and cache new instance
        logger.debug(
            "ollama_llm_instance_created",
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning=reasoning,
        )
        llm = self._init_llm(temperature=temperature, max_tokens=max_tokens, reasoning=reasoning)
        self._llm_cache[cache_key] = llm
        return llm

    def _extract_thinking_from_tags(self, content: str) -> tuple[str, str | None]:
        """Extract and strip <think>...</think> tags from content.

        Returns the cleaned content and extracted thinking text (if any).
        Validates thinking content to discard garbage (dots/spaces/punctuation).

        Args:
            content: Raw content potentially containing think tags.

        Returns:
            Tuple of (cleaned_content, thinking_text_or_none).

        """
        if not content or "<think>" not in content:
            return content, None

        # Extract thinking content from <think> tags
        thinking = None
        think_matches = _THINK_TAG_EXTRACT_PATTERN.findall(content)
        if think_matches:
            extracted = "\n".join(match.strip() for match in think_matches)
            if extracted:
                thinking = extracted
                logger.debug(
                    "ollama_thinking_extracted_from_tags",
                    provider="ollama",
                    model=self.chat_model,
                    thinking_length=len(thinking),
                )

        original_length = len(content)
        cleaned = _THINK_TAG_PATTERN.sub("", content).strip()
        if len(cleaned) < original_length:
            logger.debug(
                "ollama_think_tags_stripped",
                provider="ollama",
                model=self.chat_model,
                original_length=original_length,
                stripped_length=len(cleaned),
            )

        # Validate thinking — discard if just dots/spaces/punctuation
        if thinking:
            scrubbed = thinking.strip().replace(".", "").replace(" ", "").replace(",", "")
            if not scrubbed:
                logger.debug(
                    "ollama_thinking_discarded_garbage",
                    provider="ollama",
                    model=self.chat_model,
                    original_thinking=thinking[:50],
                )
                thinking = None

        return cleaned, thinking

    def _get_llm_for_request(
        self,
        temperature: float | None,
        max_tokens: int | None,
        *,
        enable_thinking: bool = True,
    ) -> BaseChatModel:
        """Get the right LLM instance for a request, respecting reasoning support.

        Args:
            temperature: Optional temperature override.
            max_tokens: Optional max_tokens override.
            enable_thinking: Whether to enable thinking/reasoning mode.
                When False, returns an LLM instance without reasoning
                so the model doesn't generate thinking tokens.

        Returns:
            ChatOllama instance, with or without reasoning depending on model support.

        """
        if not enable_thinking or not self._model_supports_reasoning:
            return self._get_cached_llm(temperature, max_tokens, reasoning=False)
        return self._get_cached_llm(temperature, max_tokens)

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
        """Send chat request to Ollama using LangChain."""
        # Convert messages to LangChain format
        lc_messages = convert_to_langchain_messages(messages)

        try:
            if stream:
                return cast(
                    "dict[str, Any]",
                    self._wrap_stream_with_semaphore(
                        self._stream_chat(
                            lc_messages, tools, enable_thinking, temperature, max_tokens
                        ),
                        high_priority=high_priority,
                    ),
                )

            # Non-streaming: optionally wrap with semaphore
            logger.debug(
                "ollama_request_sent",
                provider="ollama",
                model=self.chat_model,
                tools=bool(tools),
                enable_thinking=enable_thinking,
            )
            if self.enable_priority:
                async with self.semaphore.acquire(high_priority=high_priority):
                    result = await self._make_sync_request(
                        lc_messages, tools, enable_thinking, temperature, max_tokens
                    )
            else:
                result = await self._make_sync_request(
                    lc_messages, tools, enable_thinking, temperature, max_tokens
                )
            logger.debug(
                "ollama_response_received",
                provider="ollama",
                model=self.chat_model,
                content_length=len(result.get("content", "")),
                has_tool_calls=bool(result.get("tool_calls")),
                has_thinking=bool(result.get("thinking")),
            )
            return result

        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e) if str(e) else repr(e)
            logger.exception(
                "ollama_chat_failed",
                provider="ollama",
                model=self.chat_model,
                error_type=error_type,
                error_message=error_msg,
            )

            if "connect" in error_msg.lower() or "connection" in error_msg.lower():
                docker_url = "http://host.docker.internal:11434"
                msg = (
                    f"Cannot connect to Ollama at {self.base_url}. "
                    "Please ensure Ollama is running and accessible. "
                    f"If using Docker, try '{docker_url}'."
                )
                raise LLMError(msg) from e
            msg = f"Ollama error [{error_type}]: {error_msg}"
            raise LLMError(msg) from e

    def _is_tool_calling_error(self, error: Exception, has_tools: bool) -> bool:
        """Check if an error indicates tool calling is not supported.

        Args:
            error: The exception that occurred
            has_tools: Whether tools were included in the request

        Returns:
            True if this appears to be a tool calling compatibility issue

        """
        if not has_tools:
            return False

        error_str = str(error).lower()

        # Known error patterns that DEFINITIVELY indicate tool calling is not supported
        # NOTE: "unexpected end of json input" is NOT included here because it's a
        # generic server error that can happen for many reasons (timeout, memory, etc.)
        # and many models that DO support tools can trigger this error under load
        tool_error_patterns = [
            "does not support tools",
            "tool calling not supported",
            "function calling not supported",
            "tools are not supported",
            "invalid tool",
            "unknown tool",
        ]

        return any(pattern in error_str for pattern in tool_error_patterns)

    async def _invoke_with_thinking_fallback(
        self,
        llm: Any,
        messages: list,
        enable_thinking: bool,
        has_tools: bool = False,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """Invoke LLM with automatic fallback for models that don't support thinking.

        Tries with reasoning=True first (for models like Qwen3 that support thinking).
        If the model doesn't support it, automatically retries without reasoning by
        creating a new LLM instance without the reasoning parameter.

        Args:
            llm: The LangChain LLM (possibly with tools bound)
            messages: LangChain messages
            enable_thinking: Whether thinking mode was requested
            has_tools: Whether tools are being used in this request
            tools: Original tools list (needed to rebind on fallback LLM)
            temperature: Temperature setting (needed to create fallback LLM)
            max_tokens: Max tokens setting (needed to create fallback LLM)

        Returns:
            LangChain response object

        Raises:
            ToolCallingNotSupportedError: If the model doesn't support tool calling

        """
        # Handle reasoning/thinking mode:
        # NOTE: We intentionally DON'T bind reasoning=True because:
        # 1. It can suppress native <think> tag output in models like qwen3
        # 2. LangChain's reasoning_content extraction is unreliable (returns garbage)
        # Instead, we let models output <think> tags naturally and extract from content
        try:
            llm_with_reasoning = llm
            logger.debug(
                "ollama_invoke_thinking_via_tags",
                model=self.chat_model,
                enable_thinking=enable_thinking,
            )
            return await llm_with_reasoning.ainvoke(messages)
        except ResponseError as e:
            error_str = str(e)

            # Check if this is a "does not support thinking" error
            if "does not support thinking" in error_str:
                # Remember so future calls skip the reasoning attempt entirely
                self._model_supports_reasoning = False
                logger.info(
                    "ollama_reasoning_not_supported_fallback",
                    model=self.chat_model,
                    error=error_str,
                    action="reasoning_disabled_for_future_calls",
                )
                # Create a NEW LLM instance without reasoning (the original has reasoning=True baked in)
                llm_no_reasoning = self._get_cached_llm(temperature, max_tokens, reasoning=False)

                # Rebind tools if the original request had tools
                if tools:
                    llm_no_reasoning = llm_no_reasoning.bind_tools(tools, tool_choice="any")

                # Small delay to let Ollama recover from the failed request
                await asyncio.sleep(self._recovery_delay)

                try:
                    return await llm_no_reasoning.ainvoke(messages)
                except ResponseError as e2:
                    # Check if the retry also failed due to tool calling issues
                    if self._is_tool_calling_error(e2, has_tools):
                        raise ToolCallingNotSupportedError(
                            model=self.chat_model, provider="ollama"
                        ) from e2
                    raise

            # Check for tool calling errors on first attempt
            if self._is_tool_calling_error(e, has_tools):
                raise ToolCallingNotSupportedError(model=self.chat_model, provider="ollama") from e

            # Re-raise other errors
            raise

    async def _make_sync_request(
        self,
        lc_messages: list,
        tools: list[dict] | None,
        enable_thinking: bool,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Make synchronous (non-streaming) request using LangChain."""
        llm_to_use = self._get_llm_for_request(
            temperature, max_tokens, enable_thinking=enable_thinking
        )

        # Bind tools if provided
        # Use tool_choice="any" to force the model to use native tool calling
        # rather than outputting JSON as text content
        has_tools = bool(tools)
        if tools:
            llm_to_use = llm_to_use.bind_tools(tools, tool_choice="any")

        llm_with_tools = llm_to_use

        # Enable thinking mode via LangChain's reasoning parameter
        # reasoning=True extracts thinking to additional_kwargs['reasoning_content']
        # Not all models support this - we try with reasoning first, fall back without
        response = await self._invoke_with_thinking_fallback(
            llm_with_tools,
            lc_messages,
            enable_thinking,
            has_tools=has_tools,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # Extract tool calls if present
        tool_calls = None
        if hasattr(response, "tool_calls") and response.tool_calls:
            tool_calls = format_tool_calls_response(response.tool_calls)

        # Extract usage/token info, finish_reason, and provider-native timings if available
        usage = {}
        provider_timings: dict[str, Any] = {}
        raw_finish_reason: str | None = None
        if hasattr(response, "response_metadata"):
            metadata = response.response_metadata
            usage = {
                "prompt_tokens": metadata.get("prompt_eval_count", 0),
                "completion_tokens": metadata.get("eval_count", 0),
                "total_tokens": metadata.get("prompt_eval_count", 0)
                + metadata.get("eval_count", 0),
            }
            provider_timings = extract_provider_timings(response)
            # Ollama exposes the stop reason on ainvoke() via done_reason in response_metadata
            raw_finish_reason = metadata.get("done_reason")

        # Extract thinking from response
        # LangChain exposes thinking via reasoning parameter in additional_kwargs['reasoning_content']
        thinking = None
        if hasattr(response, "additional_kwargs") and response.additional_kwargs:
            thinking = response.additional_kwargs.get("reasoning_content")
            if thinking:
                logger.debug(
                    "thinking_extracted",
                    provider="ollama",
                    model=self.chat_model,
                    thinking_length=len(thinking),
                )

        # Get content from response
        content = response.content

        # Extract and strip <think>...</think> tags from content
        # For qwen3 and similar models, <think> tags are the authoritative source
        content, tag_thinking = self._extract_thinking_from_tags(content)
        if tag_thinking:
            thinking = tag_thinking  # Prefer tags over reasoning_content

        # Debug logging for empty content with non-zero completion tokens
        # Only warn if there are NO tool calls - empty content WITH tool calls is expected
        # (when using tool calling, the response goes to tool_calls, not content)
        if not content and usage.get("completion_tokens", 0) > 0 and not tool_calls:
            additional_kwargs = (
                response.additional_kwargs
                if hasattr(response, "additional_kwargs") and response.additional_kwargs
                else {}
            )
            logger.warning(
                "ollama_empty_content_with_tokens",
                provider="ollama",
                model=self.chat_model,
                completion_tokens=usage.get("completion_tokens", 0),
                has_thinking=bool(thinking),
                additional_kwargs_keys=list(additional_kwargs.keys()),
                response_type=type(response).__name__,
            )

            # Qwen3 models may put all output into reasoning_content when reasoning=False
            # but the model still decides to "think". Fall back to reasoning content as main content.
            if thinking and not content:
                logger.info(
                    "ollama_using_thinking_as_content_fallback",
                    provider="ollama",
                    model=self.chat_model,
                    thinking_length=len(thinking),
                    reason="content_empty_but_thinking_present",
                )
                content = thinking
                thinking = None  # Don't return thinking separately since we used it as content

        return {
            "content": content,
            "thinking": thinking,
            "tool_calls": tool_calls,
            "model": self.chat_model,
            "provider": "ollama",
            "usage": usage,
            "provider_timings": provider_timings,
            "finish_reason": normalize_finish_reason(raw_finish_reason),
        }

    async def _astream_with_timeout(
        self,
        llm: Any,
        messages: list,
        timeout: float | None = None,
    ) -> Any:
        """Wrap LLM astream with per-chunk timeout to detect dead connections.

        Applies a timeout between successive streaming chunks. If no data
        arrives within the timeout period, raises ``asyncio.TimeoutError``
        instead of hanging indefinitely (which happens when the Ollama model
        expires or the connection drops silently).

        Args:
            llm: LangChain chat model instance.
            messages: LangChain messages to send.
            timeout: Seconds to wait for each chunk. Defaults to STREAM_CHUNK_TIMEOUT.

        Yields:
            AIMessageChunk objects from the LLM stream.

        Raises:
            asyncio.TimeoutError: If no chunk arrives within the timeout period.

        """
        chunk_timeout = timeout or self.stream_chunk_timeout
        ait = llm.astream(messages).__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(ait.__anext__(), timeout=chunk_timeout)
                yield chunk
            except StopAsyncIteration:
                break
            except TimeoutError:
                logger.exception(
                    "ollama_stream_chunk_timeout",
                    model=self.chat_model,
                    timeout_seconds=chunk_timeout,
                    hint="Ollama may have stopped responding (model expired, OOM, or connection lost)",
                )
                raise

    async def _stream_chat(  # noqa: C901, PLR0912 - streaming chunk dispatcher; each branch handles a distinct Ollama event type
        self,
        lc_messages: list,
        tools: list[dict] | None,
        enable_thinking: bool,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """Stream chat responses using LangChain."""
        llm_to_use = self._get_llm_for_request(
            temperature, max_tokens, enable_thinking=enable_thinking
        )

        # Bind tools if provided
        if tools:
            llm_to_use = llm_to_use.bind_tools(tools, tool_choice="any")

        # Streaming accumulators
        accumulated_content = ""
        accumulated_thinking = ""
        tool_calls = None
        usage: dict[str, int] = {}
        last_chunk = None
        raw_finish_reason: str | None = None

        try:
            logger.debug(
                "ollama_stream_thinking_via_tags",
                model=self.chat_model,
                enable_thinking=enable_thinking,
            )
            first_chunk_logged = False
            async for chunk in self._astream_with_timeout(llm_to_use, lc_messages):
                last_chunk = chunk
                # Ollama emits ``done_reason`` on the final chunk's
                # response_metadata. Capture as we go so
                # ``raw_finish_reason`` reflects the last seen value.
                chunk_finish = extract_streaming_finish_reason(chunk)
                if chunk_finish:
                    raw_finish_reason = chunk_finish
                if not first_chunk_logged:
                    first_chunk_logged = True
                    logger.debug(
                        "ollama_stream_first_chunk",
                        provider="ollama",
                        model=self.chat_model,
                        chunk_type=type(chunk).__name__,
                        has_additional_kwargs=hasattr(chunk, "additional_kwargs"),
                        additional_kwargs_keys=list(chunk.additional_kwargs.keys())
                        if hasattr(chunk, "additional_kwargs") and chunk.additional_kwargs
                        else [],
                        content_preview=chunk.content[:100]
                        if hasattr(chunk, "content") and chunk.content
                        else None,
                    )

                # Extract thinking from LangChain's reasoning parameter
                if hasattr(chunk, "additional_kwargs") and chunk.additional_kwargs:
                    chunk_thinking = chunk.additional_kwargs.get("reasoning_content")
                    if chunk_thinking:
                        accumulated_thinking += chunk_thinking
                        if accumulated_thinking.strip().replace(".", "").replace(" ", ""):
                            yield {"type": "thinking_delta", "accumulated": accumulated_thinking}

                # Extract content delta
                delta_content = chunk.content if hasattr(chunk, "content") else ""
                if delta_content:
                    accumulated_content += delta_content
                    yield {
                        "type": "content",
                        "delta": delta_content,
                        "accumulated": accumulated_content,
                    }

                if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                    tool_calls = format_tool_calls_response(chunk.tool_calls)

            # Finalize: extract think tags and usage
            final_content, tag_thinking = self._extract_thinking_from_tags(accumulated_content)
            if tag_thinking:
                accumulated_thinking = tag_thinking
            elif accumulated_thinking:
                # Validate accumulated reasoning_content
                _, validated = self._extract_thinking_from_tags(
                    f"<think>{accumulated_thinking}</think>"
                )
                accumulated_thinking = validated or ""

            usage = extract_streaming_usage(last_chunk) or usage
            provider_timings = extract_provider_timings(last_chunk)

            logger.info(
                "ollama_stream_complete",
                provider="ollama",
                model=self.chat_model,
                content_length=len(final_content) if final_content else 0,
                thinking_length=len(accumulated_thinking) if accumulated_thinking else 0,
                has_tool_calls=bool(tool_calls),
            )

            yield {
                "type": "done",
                "content": final_content,
                "thinking": accumulated_thinking if accumulated_thinking else None,
                "tool_calls": tool_calls,
                "usage": usage,
                "provider_timings": provider_timings,
                "model": self.chat_model,
                "provider": "ollama",
                "finish_reason": normalize_finish_reason(raw_finish_reason),
            }

        except ResponseError as e:
            if "does not support thinking" in str(e):
                async for chunk in self._stream_with_fallback(
                    lc_messages,
                    tools,
                    temperature,
                    max_tokens,
                    accumulated_content,
                    tool_calls,
                    usage,
                ):
                    yield chunk
            else:
                logger.exception(
                    "ollama_streaming_failed",
                    provider="ollama",
                    model=self.chat_model,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                yield {"type": "error", "error": "LLM streaming failed"}

        except Exception as e:
            logger.exception(
                "ollama_streaming_failed",
                provider="ollama",
                model=self.chat_model,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            yield {"type": "error", "error": "LLM streaming failed"}

    async def _stream_with_fallback(
        self,
        lc_messages: list,
        tools: list[dict] | None,
        temperature: float | None,
        max_tokens: int | None,
        accumulated_content: str,
        tool_calls: list[dict] | None,
        usage: dict[str, int],
    ) -> Any:
        """Retry streaming without reasoning after a 'does not support thinking' error.

        Args:
            lc_messages: LangChain messages.
            tools: Tools to bind (if any).
            temperature: Temperature override.
            max_tokens: Max tokens override.
            accumulated_content: Content accumulated before failure.
            tool_calls: Tool calls accumulated before failure.
            usage: Usage dict accumulated before failure.

        Yields:
            Streaming chunks and final done chunk.

        """
        self._model_supports_reasoning = False
        logger.info(
            "ollama_stream_reasoning_not_supported_fallback",
            model=self.chat_model,
            action="reasoning_disabled_for_future_calls",
        )

        llm_no_reasoning = self._get_cached_llm(temperature, max_tokens, reasoning=False)
        if tools:
            llm_no_reasoning = llm_no_reasoning.bind_tools(tools, tool_choice="any")

        await asyncio.sleep(self._recovery_delay)

        last_chunk = None
        raw_finish_reason: str | None = None
        async for chunk in self._astream_with_timeout(llm_no_reasoning, lc_messages):
            last_chunk = chunk
            chunk_finish = extract_streaming_finish_reason(chunk)
            if chunk_finish:
                raw_finish_reason = chunk_finish
            delta_content = chunk.content if hasattr(chunk, "content") else ""
            if delta_content:
                accumulated_content += delta_content
                yield {
                    "type": "content",
                    "delta": delta_content,
                    "accumulated": accumulated_content,
                }
            if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                tool_calls = format_tool_calls_response(chunk.tool_calls)

        usage = extract_streaming_usage(last_chunk) or usage
        provider_timings = extract_provider_timings(last_chunk)
        final_content, fallback_thinking = self._extract_thinking_from_tags(accumulated_content)

        yield {
            "type": "done",
            "content": final_content,
            "thinking": fallback_thinking,
            "tool_calls": tool_calls,
            "usage": usage,
            "provider_timings": provider_timings,
            "model": self.chat_model,
            "provider": "ollama",
            "finish_reason": normalize_finish_reason(raw_finish_reason),
        }
