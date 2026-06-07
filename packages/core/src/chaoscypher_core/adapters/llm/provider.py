# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM Provider - Queue-Free Core Logic.

Extracted from LLMService for engine core usage.

This provider contains NO queue dependencies and can be used in standalone
applications without Valkey infrastructure.

Supports multi-instance Ollama with load balancing when configured.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, TypedDict

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.models import (
        HealthReport,
        LLMChatResponse,
        ToolResult,
    )
    from chaoscypher_core.settings import EngineSettings

from chaoscypher_core.adapters.llm.cost import get_cost_tracker
from chaoscypher_core.adapters.llm.factory import ProviderFactory
from chaoscypher_core.services.workflows.tools.system_tools import execute_system_tool


logger = structlog.get_logger(__name__)


class LLMManagers(TypedDict, total=False):
    """Service managers passed to LLMProvider for tool execution.

    All keys are optional since not all contexts provide all managers.
    Keys used by system_tools.execute_system_tool:
        - graph_manager: GraphRepository instance (required for tool execution)
        - search_manager: SearchRepository instance
        - discovery_service: DiscoveryService instance
        - llm_service: LLM service for recursive AI tool calls
    Keys used by neuron worker setup:
        - graph: GraphRepository instance
        - search: SearchRepository instance
        - config: Configuration manager
    """

    graph_manager: Any
    search_manager: Any
    discovery_service: Any
    llm_service: Any
    graph: Any
    search: Any
    config: Any


class LLMProvider:
    """Queue-free LLM provider for direct API calls.

    This class provides direct access to LLM operations without queue coordination.
    Suitable for:
    - Engine core services (extraction, discovery, lenses)
    - CLI applications
    - Testing and development
    - Synchronous execution contexts

    For queue-based execution (web APIs, background workers), use LLMQueueService.
    """

    def __init__(self, settings: EngineSettings | None = None, managers: LLMManagers | None = None):
        """Initialize LLM provider.

        Args:
            settings: Application settings (must have .llm attributes).
                When None, creates a default EngineSettings instance.
            managers: Typed dict of service managers for tool execution

        """
        if settings is None:
            from chaoscypher_core.settings import EngineSettings

            settings = EngineSettings()

        self.settings = settings
        self.managers: LLMManagers = managers or {}
        self._provider_factory = ProviderFactory(settings)

        # Initialize cost tracker with custom costs from settings if enabled
        custom_input = (
            settings.llm.token_cost_input_per_million
            if settings.llm.enable_token_cost_tracking
            else 0.0
        )
        custom_output = (
            settings.llm.token_cost_output_per_million
            if settings.llm.enable_token_cost_tracking
            else 0.0
        )
        self.cost_tracker = get_cost_tracker(custom_input, custom_output)

        logger.info("llm_provider_initialized", mode="queue_free")

    def _uses_load_balancer(self) -> bool:
        """Check if load balancer should be used for chat requests.

        Returns:
            True if load balancer should be used (Ollama with multiple instances)
        """
        # Check if the provider is Ollama
        if self.settings.llm.chat_provider != "ollama":
            return False

        # Check if multiple instances are configured
        instances = self.settings.llm.ollama_instances or []
        return len(instances) > 1  # Load balancer only makes sense with multiple instances

    async def reload_load_balancer(self) -> None:
        """Reload load balancer with current settings (for hot reload support)."""
        await self._provider_factory.reload_load_balancer()

    async def check_health(self) -> HealthReport:
        """Check health of the configured chat LLM provider.

        Tests the chat provider with a minimal request to verify
        connectivity, credentials, and model access.

        Returns:
            HealthReport with chat health result.

        Example:
            health = await llm.check_health()
            print(f"Chat: {health.chat.status}")

        """
        from chaoscypher_core.models import HealthReport, HealthResult

        chat_health = await self._provider_factory.check_provider_health("chat")

        # Construct with explicit named args — check_provider_health() may return
        # extra keys like "error_type" that HealthResult doesn't accept
        def _to_health_result(data: dict[str, Any]) -> HealthResult:
            """Narrow a raw provider health dict into the typed HealthResult."""
            return HealthResult(
                status=data.get("status", "unhealthy"),
                provider=data.get("provider"),
                model=data.get("model"),
                embedding_dimensions=data.get("embedding_dimensions"),
                response_time_ms=data.get("response_time_ms"),
                error=data.get("error"),
            )

        return HealthReport(
            chat=_to_health_result(chat_health),
        )

    # ========================================================================
    # Usage & Cost Helpers
    # ========================================================================

    def _normalize_usage(self, raw_usage: dict[str, Any]) -> dict[str, int]:
        """Normalize usage dict to consistent format.

        Handles both OpenAI-style (prompt_tokens/completion_tokens) and
        Anthropic-style (input_tokens/output_tokens) key formats.

        Args:
            raw_usage: Raw usage dict from provider response.

        Returns:
            Dict with input_tokens, output_tokens, total_tokens.

        """
        input_tokens = raw_usage.get("input_tokens", 0) or raw_usage.get("prompt_tokens", 0)
        output_tokens = raw_usage.get("output_tokens", 0) or raw_usage.get("completion_tokens", 0)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }

    def _attach_cost(
        self, usage: dict[str, Any], provider: str, model_key: str, **kwargs: Any
    ) -> None:
        """Calculate and attach cost to usage dict in-place.

        Args:
            usage: Normalized usage dict (modified in-place with cost_usd key).
            provider: Provider name (e.g. "ollama", "openai").
            model_key: Settings attribute name for the model (e.g. "ollama_chat_model").
            **kwargs: Additional kwargs to check for model override.

        """
        if not self.settings.llm.enable_token_cost_tracking:
            return
        model_raw = kwargs.get("model") or getattr(self.settings.llm, model_key, "unknown")
        model = str(model_raw) if model_raw is not None else "unknown"
        cost = self.cost_tracker.calculate_cost(
            provider=provider,
            model=model,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
        usage["cost_usd"] = cost

    # ========================================================================
    # Response Normalization Helpers
    # ========================================================================

    def _build_chat_response(
        self, response: dict[str, Any], instance_id: str | None = None, **kwargs: Any
    ) -> LLMChatResponse:
        """Build an LLMChatResponse model from a provider response.

        Extracts content, tool_calls, and thinking from the raw response,
        normalizes usage statistics, and attaches cost tracking.

        Args:
            response: Raw response dict from a chat provider.
            instance_id: Load balancer instance ID (if using load balancer).
            **kwargs: Additional kwargs forwarded to cost attachment (e.g., model override).

        Returns:
            LLMChatResponse model with content, tool_calls, thinking, usage, and provider.

        """
        from chaoscypher_core.models import LLMChatResponse, TokenUsage

        content = response.get("content", "")
        tool_calls = response.get("tool_calls")
        thinking = response.get("thinking")

        # Normalize usage and attach cost
        raw_usage = self._normalize_usage(response.get("usage", {}))
        self._attach_cost(
            raw_usage,
            self.settings.llm.chat_provider,
            f"{self.settings.llm.chat_provider}_chat_model",
            **kwargs,
        )

        # Construct TokenUsage with explicit named args to avoid fragility
        usage = TokenUsage(
            input_tokens=raw_usage.get("input_tokens", 0),
            output_tokens=raw_usage.get("output_tokens", 0),
            total_tokens=raw_usage.get("total_tokens", 0),
            cost_usd=raw_usage.get("cost_usd"),
        )

        return LLMChatResponse(
            content=content,
            tool_calls=tool_calls,
            thinking=thinking,
            usage=usage,
            provider=self.settings.llm.chat_provider,
            is_stream=False,
            instance_id=instance_id,
            finish_reason=response.get("finish_reason"),
        )

    # ========================================================================
    # Core LLM Operations (Direct API Calls)
    # ========================================================================

    async def chat(
        self,
        messages: str | list[Any],
        tools: list[Any] | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> LLMChatResponse:
        """Execute chat completion directly (no queue).

        Args:
            messages: A string prompt (auto-wrapped as user message) or
                chat messages in OpenAI format.
            tools: Optional list of tools for function calling
            stream: Enable streaming (returns async generator)
            **kwargs: Additional parameters (temperature, max_tokens, enable_thinking, etc.)

        Returns:
            LLMChatResponse with content, tool_calls, thinking, usage, provider,
            is_stream flag, and stream generator (if streaming).

        Raises:
            Exception: If chat completion fails

        """
        # String shorthand: wrap as a single user message
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        logger.info("chat_completion_executing", message_count=len(messages))
        _start = time.monotonic()

        # Extract enable_thinking from kwargs or metadata
        enable_thinking = kwargs.get("enable_thinking", False)
        if not enable_thinking and "metadata" in kwargs:
            enable_thinking = kwargs["metadata"].get("thinking_enabled", False)

        # Check if we should use load balancer (Ollama with instances configured)
        # NOTE: Streaming requests bypass load balancer because the semaphore would be
        # released before the stream is consumed. Streaming uses the global semaphore
        # handled by the provider internally.
        if self._uses_load_balancer() and not stream:
            return await self._chat_with_load_balancer(
                messages=messages,
                tools=tools,
                stream=stream,
                enable_thinking=enable_thinking,
                **kwargs,
            )

        # Use standard single-provider path
        try:
            chat_provider = self._provider_factory.get_chat_provider()

            logger.info(
                "llm_chat_completion_executing",
                provider=self.settings.llm.chat_provider,
                thinking_enabled=enable_thinking,
                has_tools=bool(tools),
                stream=stream,
            )

            # Execute chat via provider
            response = await chat_provider.chat(
                messages=messages, tools=tools, stream=stream, enable_thinking=enable_thinking
            )

            # Handle streaming response
            if stream and hasattr(response, "__aiter__"):
                from chaoscypher_core.models import LLMChatResponse

                logger.info("llm_chat_completion_returning_stream", is_stream=True)
                return LLMChatResponse(
                    content="",
                    provider=self.settings.llm.chat_provider,
                    is_stream=True,
                    stream=response,
                )

            # Non-streaming: build normalized response
            chat_result = self._build_chat_response(response, **kwargs)

            logger.info(
                "chat_completion_completed",
                content_length=len(chat_result.content),
                has_tool_calls=bool(chat_result.tool_calls),
                has_thinking=bool(chat_result.thinking),
                input_tokens=chat_result.usage.input_tokens if chat_result.usage else 0,
                output_tokens=chat_result.usage.output_tokens if chat_result.usage else 0,
                duration_ms=int((time.monotonic() - _start) * 1000),
            )

            return chat_result

        except Exception as e:
            logger.exception(
                "chat_completion_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    async def _chat_with_load_balancer(
        self,
        messages: list[Any],
        tools: list[Any] | None = None,
        stream: bool = False,
        enable_thinking: bool = False,
        **kwargs: Any,
    ) -> LLMChatResponse:
        """Execute chat using load balancer for multi-instance Ollama.

        Acquires an instance from the load balancer, executes the chat,
        and releases the instance slot when done.

        NOTE: Streaming is NOT supported via load balancer because the semaphore
        would be released before the stream is consumed. Streaming requests should
        bypass the load balancer and use the standard single-provider path.
        """
        # Safeguard: streaming should never reach this method
        if stream:
            msg = "Streaming not supported via load balancer - use standard provider path"
            raise ValueError(msg)

        load_balancer = await self._provider_factory.get_load_balancer()

        # Determine priority from kwargs
        high_priority = kwargs.pop("high_priority", False)

        try:
            async with load_balancer.acquire_instance(high_priority=high_priority) as (
                instance_id,
                chat_provider,
            ):
                logger.info(
                    "llm_chat_executing_via_load_balancer",
                    instance_id=instance_id,
                    thinking_enabled=enable_thinking,
                    has_tools=bool(tools),
                    high_priority=high_priority,
                )

                # Execute chat via the acquired provider
                # Note: The provider's internal semaphore is bypassed since load balancer handles concurrency
                response = await chat_provider.chat(
                    messages=messages,
                    tools=tools,
                    stream=False,  # Explicitly disable streaming
                    enable_thinking=enable_thinking,
                    high_priority=high_priority,  # Pass through for provider semaphore
                )

                # Build normalized response
                chat_result = self._build_chat_response(response, instance_id=instance_id, **kwargs)

                logger.info(
                    "llm_chat_completed_via_load_balancer",
                    instance_id=instance_id,
                    content_length=len(chat_result.content),
                    has_tool_calls=bool(chat_result.tool_calls),
                    input_tokens=chat_result.usage.input_tokens if chat_result.usage else 0,
                    output_tokens=chat_result.usage.output_tokens if chat_result.usage else 0,
                )

                return chat_result

        except Exception as e:
            logger.exception(
                "chat_via_load_balancer_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    async def execute_tool(
        self, tool_name: str, tool_input: dict[str, Any], **kwargs: Any
    ) -> ToolResult:
        """Execute tool directly (no queue).

        Args:
            tool_name: Name of the tool to execute
            tool_input: Tool input parameters
            **kwargs: Additional parameters

        Returns:
            ToolResult with execution result and tool name.

        Raises:
            Exception: If tool execution fails

        """
        logger.info("tool_executing", tool_name=tool_name)

        try:
            result = await execute_system_tool(
                tool_name=tool_name,
                tool_input=tool_input,
                managers=dict(self.managers),
                settings=self.settings,
            )

            logger.info("tool_executed", tool_name=tool_name)

            from chaoscypher_core.models import ToolResult

            return ToolResult(result=result, tool_name=tool_name)

        except Exception as e:
            logger.exception(
                "tool_execution_failed",
                tool_name=tool_name,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise
