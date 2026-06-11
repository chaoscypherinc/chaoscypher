# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Base LLM Provider using LangChain.

Abstract base class for all LLM providers. Handles:
- Semaphore integration for priority-based concurrency control
- Streaming wrapper for async generators
- Common interface for chat
- Shared streaming token usage extraction

All concrete providers must implement:
- _init_llm() - Initialize LangChain chat model
- chat() - Chat completion (streaming and non-streaming)
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from chaoscypher_core.adapters.llm.limit import get_llm_semaphore


if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from chaoscypher_core.plugins.base import PluginMetadata

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------ #
#  Streaming token usage extraction
# ------------------------------------------------------------------ #


def _get_usage_value(usage_meta: Any, field: str) -> int:
    """Extract an integer value from a usage metadata object.

    Handles both dict-style and attribute-style access patterns used by
    different LangChain provider implementations.

    Args:
        usage_meta: Usage metadata (dict or object with attributes).
        field: Field name to extract.

    Returns:
        Integer value, defaulting to 0 if missing or falsy.

    """
    raw = (
        usage_meta.get(field, 0) if isinstance(usage_meta, dict) else getattr(usage_meta, field, 0)
    )
    return raw or 0


def extract_streaming_usage(chunk: Any) -> dict[str, int]:  # noqa: PLR0911
    """Extract token usage from a LangChain streaming chunk's metadata.

    During streaming, individual chunks don't carry usage data.  The
    *last* ``AIMessageChunk`` emitted by LangChain, however, typically
    includes usage information in ``usage_metadata`` (standardized in
    LangChain 1.0+) or in provider-specific ``response_metadata``.

    This helper tries all known patterns so callers don't need
    provider-specific extraction logic.

    Args:
        chunk: The last AIMessageChunk from streaming, or None.

    Returns:
        Dict with prompt_tokens, completion_tokens, total_tokens.
        Empty dict if no usage data found.

    """
    if chunk is None:
        return {}

    # 1. LangChain standardized usage_metadata (input_tokens / output_tokens)
    um = getattr(chunk, "usage_metadata", None)
    if um:
        input_t = _get_usage_value(um, "input_tokens")
        output_t = _get_usage_value(um, "output_tokens")
        total_t = _get_usage_value(um, "total_tokens")
        if input_t or output_t:
            return {
                "prompt_tokens": input_t,
                "completion_tokens": output_t,
                "total_tokens": total_t or (input_t + output_t),
            }

    # 2. Provider-specific response_metadata fallbacks
    metadata = getattr(chunk, "response_metadata", None)
    if not metadata:
        return {}

    # OpenAI: nested token_usage dict
    if "token_usage" in metadata:
        tu = metadata["token_usage"]
        return {
            "prompt_tokens": tu.get("prompt_tokens", 0),
            "completion_tokens": tu.get("completion_tokens", 0),
            "total_tokens": tu.get("total_tokens", 0),
        }

    # Ollama: flat prompt_eval_count / eval_count
    if "prompt_eval_count" in metadata or "eval_count" in metadata:
        prompt_t = metadata.get("prompt_eval_count", 0) or 0
        eval_t = metadata.get("eval_count", 0) or 0
        return {
            "prompt_tokens": prompt_t,
            "completion_tokens": eval_t,
            "total_tokens": prompt_t + eval_t,
        }

    # Gemini: flat prompt_token_count / candidates_token_count
    if "prompt_token_count" in metadata or "candidates_token_count" in metadata:
        return {
            "prompt_tokens": metadata.get("prompt_token_count", 0) or 0,
            "completion_tokens": metadata.get("candidates_token_count", 0) or 0,
            "total_tokens": metadata.get("total_token_count", 0) or 0,
        }

    return {}


# ------------------------------------------------------------------ #
#  Streaming finish-reason normalization
# ------------------------------------------------------------------ #


# Maps every provider's raw finish-reason value to a stable vocabulary
# used by extraction observability. Anything not in the map normalizes
# to "unknown".
_FINISH_REASON_MAP: dict[str, str] = {
    # OpenAI / OpenAI-compatible
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "content_filter": "content_filter",
    # Anthropic
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    # Ollama done_reason values
    "load": "stop",
    "unload": "stop",
    # Gemini (uppercase enum-style)
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "SPII": "content_filter",
    "MALFORMED_FUNCTION_CALL": "error",
    "OTHER": "unknown",
    # Lowercase aliases sometimes seen in adapter glue
    "safety": "content_filter",
    "recitation": "content_filter",
}


def normalize_finish_reason(raw: Any) -> str:
    """Map a provider-specific finish reason to the stable vocabulary.

    Stable values: ``stop``, ``length``, ``content_filter``,
    ``tool_calls``, ``error``, ``unknown``.

    ``None`` and unrecognized values normalize to ``"unknown"`` so the
    caller can always store a non-null token. The raw value is otherwise
    passed through ``str()`` before lookup so providers that emit enum
    objects (Gemini's ``FinishReason`` enum) round-trip cleanly.

    Args:
        raw: Any provider-specific finish-reason value.

    Returns:
        One of the six stable vocabulary tokens.
    """
    if raw is None:
        return "unknown"
    key = str(raw)
    return _FINISH_REASON_MAP.get(key, "unknown")


def extract_streaming_finish_reason(chunk: Any) -> str | None:  # noqa: PLR0911 - one return per provider's metadata shape
    """Pull a finish reason off the last LangChain streaming chunk.

    Looks in the standardized ``response_metadata`` first (where most
    LangChain providers stash it), then falls back to the chunk's
    ``finish_reason`` attribute. Returns the *raw* provider value so
    callers can decide whether to normalize.

    Args:
        chunk: The last AIMessageChunk from streaming, or None.

    Returns:
        Raw finish reason string, or None when the chunk doesn't carry one.
    """
    if chunk is None:
        return None

    metadata = getattr(chunk, "response_metadata", None)
    if metadata:
        # OpenAI: top-level "finish_reason"
        finish = metadata.get("finish_reason")
        if finish:
            return str(finish)
        # Anthropic: "stop_reason" on message_stop
        stop_reason = metadata.get("stop_reason")
        if stop_reason:
            return str(stop_reason)
        # Ollama exposes a done_reason field
        done_reason = metadata.get("done_reason")
        if done_reason:
            return str(done_reason)
        # Gemini: nested under candidates → finish_reason
        candidates = metadata.get("candidates")
        if isinstance(candidates, list) and candidates:
            cand = candidates[0]
            if isinstance(cand, dict):
                finish = cand.get("finish_reason")
                if finish:
                    return str(finish)

    # Direct attribute fallback (some adapters set chunk.finish_reason).
    direct = getattr(chunk, "finish_reason", None)
    if direct:
        return str(direct)
    return None


def extract_provider_timings(chunk: Any) -> dict[str, Any]:
    """Extract provider-native timing data from a LangChain streaming chunk.

    Ollama returns ``eval_duration`` and ``prompt_eval_duration`` (nanoseconds)
    in ``response_metadata``.  These give the exact server-side generation and
    prompt-evaluation times, which are far more accurate than wall-clock
    estimates for computing tokens/sec.

    Args:
        chunk: The last AIMessageChunk from streaming, or None.

    Returns:
        Dict with ``eval_duration_ns``, ``prompt_eval_duration_ns``, and
        ``eval_count`` when available.  Empty dict otherwise.

    """
    if chunk is None:
        return {}

    metadata = getattr(chunk, "response_metadata", None)
    if not metadata:
        return {}

    timings: dict[str, Any] = {}

    if "eval_duration" in metadata:
        timings["eval_duration_ns"] = metadata["eval_duration"]
    if "prompt_eval_duration" in metadata:
        timings["prompt_eval_duration_ns"] = metadata["prompt_eval_duration"]
    if "eval_count" in metadata:
        timings["eval_count"] = metadata["eval_count"]

    return timings


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers using LangChain.

    This class provides:
    - Semaphore integration for priority-based concurrency control
    - Streaming wrapper that respects semaphore boundaries
    - Common initialization pattern

    Subclasses must implement:
    - _init_llm() -> BaseChatModel
    - chat() -> chat completion
    """

    # Concrete subclasses set this; ProviderRegistry reads it via getattr.
    _METADATA: ClassVar[PluginMetadata]

    def __init__(self, config: dict[str, Any]):
        """Initialize base provider.

        Args:
            config: Configuration dictionary with provider settings

        """
        self.config = config
        self.llm: BaseChatModel | None = None

        # Get the existing semaphore singleton, initialized with config values
        # This ensures the semaphore uses llm_max_concurrent from settings
        max_concurrent = config["llm_max_concurrent"]
        reserved_high_priority = config["llm_reserved_interactive"]
        self.semaphore = get_llm_semaphore(
            max_concurrent=max_concurrent,
            reserved_high_priority=reserved_high_priority,
        )
        self.enable_priority = config["llm_enable_priority"]

        if not self.enable_priority:
            logger.warning(
                "llm_priority_disabled",
                provider=config["chat_provider"],
                action="all_requests_bypass_semaphore",
            )

    def _effective_max_tokens(self, provider_cap_key: str) -> int | None:
        """Resolve the request max_tokens from generic + provider caps.

        The generic ``ai_max_tokens`` is bounded by the provider-specific
        output cap (e.g. ``anthropic_max_output_tokens``) when that is set —
        the Settings sliders were previously defined but unwired
        (2026-06-10 audit). Either knob alone applies as-is; both unset
        means no explicit limit.

        Args:
            provider_cap_key: Config key of the provider's output cap.

        Returns:
            The effective max output tokens, or None when neither knob is set.

        """
        candidates = [
            value
            for value in (self.config.get("ai_max_tokens"), self.config.get(provider_cap_key))
            if isinstance(value, int) and value > 0
        ]
        return min(candidates) if candidates else None

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Provider descriptor consumed by :class:`ProviderRegistry`.

        Must be a :class:`PluginMetadata` instance (see
        ``chaoscypher_core.plugins.base``). Subclasses are expected to
        return a cached instance — either a ``ClassVar`` or an attribute
        set in ``__init__`` — so the registry's origin-tagging survives
        subsequent property accesses.
        """

    @abstractmethod
    def _init_llm(self) -> BaseChatModel:
        """Initialize the LangChain chat model.

        Returns:
            Initialized LangChain BaseChatModel instance

        """

    async def _wrap_stream_with_semaphore(self, stream_generator: Any, high_priority: bool) -> Any:
        """Wrap an async generator with semaphore management.

        Acquires the semaphore before streaming starts and releases after completion.
        This ensures streaming respects concurrency limits.

        Args:
            stream_generator: Async generator that yields chunks
            high_priority: Whether this is a high-priority request (interactive chat)

        Yields:
            Chunks from the stream_generator

        """
        if not self.enable_priority:
            # Priority disabled, just pass through
            async for chunk in stream_generator:
                yield chunk
            return

        # Acquire semaphore for the duration of the stream
        logger.debug("acquiring_semaphore", high_priority=high_priority)
        async with self.semaphore.acquire(high_priority=high_priority):
            logger.debug("semaphore_acquired", high_priority=high_priority, operation="streaming")
            try:
                chunk_count = 0
                async for chunk in stream_generator:
                    chunk_count += 1
                    if chunk_count <= 2:
                        logger.debug("yielding_chunk", chunk_number=chunk_count)
                    yield chunk
                logger.debug("stream_complete", chunk_count=chunk_count)
            finally:
                logger.debug("releasing_semaphore", chunk_count=chunk_count)

    @abstractmethod
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
        """Send chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            tools: Optional list of tool definitions (OpenAI format)
            stream: Whether to stream the response
            enable_thinking: Enable reasoning/thinking mode for supported models
            high_priority: If True, gets priority in LLM semaphore (for interactive chat)
            temperature: Override temperature for this request
            max_tokens: Override max tokens for this request

        Returns:
            Response dict with 'content', 'tool_calls', etc. (or async generator if streaming)

        """

    async def check_health(self) -> bool:
        """Check if the LLM provider is responsive.

        Used for health checks before retrying failed requests.
        Default implementation returns True (assumes healthy).
        Subclasses can override to implement provider-specific health checks.

        Returns:
            True if provider is healthy and responsive, False otherwise.

        """
        return True

    async def close(self) -> None:
        """Close provider connections (cleanup).

        LangChain providers typically don't require explicit cleanup,
        but this method is provided for compatibility with streaming
        contexts that expect a close() method.

        Default no-op implementation - subclasses can override if needed.
        """
        return  # No-op for most providers (LangChain handles cleanup automatically)
