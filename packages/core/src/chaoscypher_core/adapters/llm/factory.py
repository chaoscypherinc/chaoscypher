# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM Provider Factory.

Factory for creating and caching LLM provider instances.
Handles provider selection, lifecycle management, and fallbacks.

Every chat provider it hands out (``get_chat_provider``,
``get_extraction_provider``, ``acquire_instance``) sits behind the completion
cache (:func:`chaoscypher_core.cache.wiring.wrap_chat`) unless
``settings.cache.completions.enabled`` is off.

This is infrastructure code (not a VSA service layer).
Services that need LLM providers should use this factory.
"""

from __future__ import annotations

import time
import weakref
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from chaoscypher_core.cache.chat import ChatBuildResolver
    from chaoscypher_core.settings import EngineSettings

import structlog

from chaoscypher_core.adapters.llm.load_balancer import (
    OllamaLoadBalancer,
    get_ollama_load_balancer,
)
from chaoscypher_core.adapters.llm.providers import BaseLLMProvider, get_provider, ollama_provider
from chaoscypher_core.adapters.llm.providers.ollama_provider import ollama_runtime_config
from chaoscypher_core.cache.wiring import digest_build_resolver, unwrap_chat, wrap_chat


logger = structlog.get_logger(__name__)


async def _ollama_build_report(base_url: str, model: str) -> Mapping[str, Any]:
    """What the Ollama server at ``base_url`` says about ``model`` (read at call time, so fakeable)."""
    return await ollama_provider.ollama_model_build(base_url, model)


def chat_build_resolver(provider: Any) -> ChatBuildResolver | None:
    """The model-build resolver the completion cache keys ``provider`` on.

    An Ollama provider's build is its model's digest and the server version,
    read from its own server on the first call (the cache bypasses when the
    server cannot say); a hosted provider has no digest, so None (the cache
    keys on ``provider:model``, a dated model id).
    """
    config = getattr(provider, "config", None)
    if not isinstance(config, Mapping) or config.get("chat_provider") != "ollama":
        return None
    base_url = config.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        return None
    return digest_build_resolver("ollama", base_url, _ollama_build_report)


class ProviderFactory:
    """Factory for creating and caching LLM providers.

    Responsibilities (Infrastructure):
    - Create providers from settings configuration
    - Cache provider instances for connection reuse
    - Manage provider lifecycle
    - Manage Ollama load balancer for multi-instance setups

    This is NOT a VSA service layer - it's shared infrastructure.
    Business logic belongs in feature services that use this factory.
    """

    def __init__(self, settings: EngineSettings) -> None:
        """Initialize Provider Factory.

        Args:
            settings: Settings object with LLM configuration

        """
        self.settings = settings
        self._provider_cache: dict[str, BaseLLMProvider] = {}
        self._load_balancer: OllamaLoadBalancer | None = None
        self._load_balancer_initialized: bool = False
        # The cache wrapper of each balancer provider this factory acquired:
        # one per provider, so its resolved model build is kept between turns.
        self._instance_wrappers: weakref.WeakKeyDictionary[Any, BaseLLMProvider] = (
            weakref.WeakKeyDictionary()
        )

    def _cached(self, provider: BaseLLMProvider) -> BaseLLMProvider:
        """``provider`` behind the completion cache this factory's settings ask for."""
        return wrap_chat(provider, self.settings, build_resolver=chat_build_resolver(provider))

    def _cached_instance(self, provider: BaseLLMProvider) -> BaseLLMProvider:
        """A balancer provider behind this factory's completion cache (one wrapper per provider).

        Wrapped here, not where the balancer builds it: the balancer is
        process-wide and sees only ``LLMSettings``, so a wrapper built there
        would serve one factory's cache settings (on, off, replay) to every
        other factory sharing the pool (the audit wave 6 bug class).
        """
        wrapped = self._instance_wrappers.get(provider)
        if wrapped is None:
            wrapped = self._cached(provider)
            if wrapped is not provider:  # a value holding its key would never be collected
                self._instance_wrappers[provider] = wrapped
        return wrapped

    def get_chat_provider(self) -> BaseLLMProvider:
        """Get the configured chat provider (cached for session reuse).

        Returns:
            Initialized BaseLLMProvider instance (behind the completion cache
            when ``settings.cache.completions.enabled``)

        Example:
            >>> factory = ProviderFactory(settings)
            >>> provider = factory.get_chat_provider()
            >>> response = await provider.chat(messages)

        """
        provider_type = self.settings.llm.chat_provider
        cache_key = f"chat:{provider_type}"

        # Return cached provider if available
        if cache_key in self._provider_cache:
            return self._provider_cache[cache_key]

        # Create new provider and cache it
        config = self._get_provider_config()
        config["chat_provider"] = provider_type  # Add provider type to config

        provider = self._cached(get_provider(config))
        self._provider_cache[cache_key] = provider
        logger.info("chat_provider_cached", provider_type=provider_type)
        return provider

    def get_extraction_provider(self) -> BaseLLMProvider:
        """Get provider configured for extraction (uses extraction model if set).

        Uses the extraction-specific model for each provider if configured,
        otherwise falls back to the chat model. This allows users to use a
        larger/smarter model for extraction while using a faster model for chat.

        Returns:
            Initialized BaseLLMProvider instance for extraction (behind the
            completion cache when ``settings.cache.completions.enabled``)

        Example:
            >>> factory = ProviderFactory(settings)
            >>> provider = factory.get_extraction_provider()
            >>> response = await provider.chat(messages, tools=None)

        """
        provider_type = self.settings.llm.chat_provider
        cache_key = f"extraction:{provider_type}"

        # Return cached provider if available
        if cache_key in self._provider_cache:
            return self._provider_cache[cache_key]

        # Create new provider with extraction config
        config = self._get_extraction_config()
        config["chat_provider"] = provider_type

        provider = self._cached(get_provider(config))
        self._provider_cache[cache_key] = provider
        logger.debug("extraction_provider_created", provider_type=provider_type)
        return provider

    def _get_extraction_config(self) -> dict[str, Any]:
        """Get config with extraction models (falls back to chat models).

        Returns:
            Dictionary with provider configuration, using extraction models where configured

        """
        llm = self.settings.llm
        base_config = self._get_provider_config()

        # Override chat models with extraction models where configured
        if llm.ollama_extraction_model:
            base_config["ollama_chat_model"] = llm.ollama_extraction_model
        if llm.openai_extraction_model:
            base_config["openai_chat_model"] = llm.openai_extraction_model
        if llm.anthropic_extraction_model:
            base_config["anthropic_chat_model"] = llm.anthropic_extraction_model
        if llm.gemini_extraction_model:
            base_config["gemini_chat_model"] = llm.gemini_extraction_model

        return base_config

    async def check_provider_health(self, provider_type: str | None = None) -> dict[str, Any]:
        """Check if the configured chat provider is reachable and functioning.

        Tests the chat provider with a minimal request to verify
        connectivity, credentials, model access, and basic completion.

        Args:
            provider_type: Provider to check. Only 'chat' is supported.
                          Defaults to 'chat'.

        Returns:
            Dictionary with health check results including status, provider,
            model, response_time_ms, and error (if unhealthy).

        """
        provider_type = provider_type or "chat"
        test_model = "unknown"

        try:
            if provider_type == "chat":
                # the model itself: a health check served from the cache proves nothing
                provider = unwrap_chat(self.get_chat_provider())
                test_model = self.settings.llm.chat_provider
                start_time = time.time()
                response = await provider.chat(
                    messages=[{"role": "user", "content": "hi"}],
                    stream=False,
                    enable_thinking=False,
                )
                elapsed_ms = int((time.time() - start_time) * 1000)

                if response and ("content" in response or "response" in response):
                    return {
                        "status": "healthy",
                        "provider": test_model,
                        "model": response.get("model", "unknown"),
                        "response_time_ms": elapsed_ms,
                    }
                return {
                    "status": "unhealthy",
                    "provider": test_model,
                    "error": "Provider returned invalid response",
                }

            return {
                "status": "unhealthy",
                "provider": None,
                "error": f"Invalid provider_type: {provider_type}",
            }

        except Exception as e:
            logger.exception(
                "provider_health_check_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {
                "status": "unhealthy",
                "provider": test_model,
                "error": "Health check failed",
            }

    def _get_provider_config(self) -> dict[str, Any]:
        """Extract provider config from settings.

        Returns:
            Dictionary with all provider configuration

        """
        # Settings are nested under settings.llm.*
        llm = self.settings.llm

        # LLMSettings fields already have proper defaults defined in settings.py.
        # Do NOT add fallback values here -- they would contradict the source of truth.
        return {
            # Ollama — `base_url` is the per-instance URL the OllamaProvider
            # reads at __init__ time. We use the primary instance for the
            # single-provider (non-load-balanced) code path.
            "base_url": llm.primary_ollama_url,
            "ollama_chat_model": llm.ollama_chat_model,
            **ollama_runtime_config(llm),
            # OpenAI
            "openai_api_key": (
                llm.openai_api_key.get_secret_value() if llm.openai_api_key else None
            ),
            "openai_base_url": llm.openai_base_url,
            "openai_chat_model": llm.openai_chat_model,
            "openai_context_window": llm.openai_context_window,
            "openai_max_output_tokens": llm.openai_max_output_tokens,
            # Anthropic
            "anthropic_api_key": (
                llm.anthropic_api_key.get_secret_value() if llm.anthropic_api_key else None
            ),
            "anthropic_chat_model": llm.anthropic_chat_model,
            "anthropic_context_window": llm.anthropic_context_window,
            "anthropic_max_output_tokens": llm.anthropic_max_output_tokens,
            # Gemini
            "gemini_api_key": (
                llm.gemini_api_key.get_secret_value() if llm.gemini_api_key else None
            ),
            "gemini_chat_model": llm.gemini_chat_model,
            "gemini_context_window": llm.gemini_context_window,
            "gemini_max_output_tokens": llm.gemini_max_output_tokens,
            # General LLM settings
            "ai_temperature": llm.ai_temperature,
            "ai_max_tokens": llm.ai_max_tokens,
            "seed": llm.seed,
            # Ollama health check + recovery tuning
            "ollama_health_check_timeout": llm.ollama_health_check_timeout,
            "ollama_recovery_delay": llm.ollama_recovery_delay,
            # Stream chunk timeout (dead connection detection)
            "stream_chunk_timeout": llm.stream_chunk_timeout,
            # Bounded per-request LangChain timeout (non-queued call safety net)
            "llm_request_timeout": llm.llm_request_timeout,
            # LLM Priority Semaphore settings
            "llm_max_concurrent": llm.llm_max_concurrent,
            "llm_reserved_interactive": llm.llm_reserved_interactive,
            "llm_enable_priority": llm.llm_enable_priority,
        }

    # ========================================================================
    # Multi-Instance Ollama Support
    # ========================================================================

    async def get_load_balancer(self) -> OllamaLoadBalancer:
        """Get the Ollama load balancer (initialized and configured).

        Loads the pool these settings ask for unless it is the one loaded
        (:meth:`OllamaLoadBalancer.ensure_loaded`: one check-and-reload under
        the balancer's lock). The balancer is process-wide, and another
        factory may want another model, seed or temperature: acquire through
        :meth:`acquire_instance`, which hands back a provider built from this
        factory's settings (audit wave 6: a reload then an acquire as two
        steps let two configs get each other's provider).

        Returns:
            Configured OllamaLoadBalancer instance
        """
        if self._load_balancer is None:
            self._load_balancer = get_ollama_load_balancer()
        await self._load_balancer.ensure_loaded(self.settings.llm)
        self._load_balancer_initialized = True
        return self._load_balancer

    @asynccontextmanager
    async def acquire_instance(
        self, *, high_priority: bool = False
    ) -> AsyncIterator[tuple[str, BaseLLMProvider]]:
        """An instance slot and the provider built from this factory's settings on it.

        The provider is keyed by this factory's config fingerprint, so a pool
        loaded since by another config never answers this factory's request,
        and sits behind this factory's completion cache
        (:meth:`_cached_instance`).

        Yields:
            ``(instance_id, provider)`` while the instance's slot is held.
        """
        balancer = await self.get_load_balancer()
        async with balancer.acquire_instance(
            high_priority=high_priority, llm_settings=self.settings.llm
        ) as (instance_id, provider):
            yield instance_id, self._cached_instance(provider)

    async def reload_load_balancer(self) -> None:
        """Reload the load balancer configuration from current settings.

        Call this after settings have been updated to hot-reload
        the instance pool without restart.
        """
        if self._load_balancer is not None:
            await self._load_balancer.reload_config(self.settings.llm)
            logger.info("load_balancer_reloaded_via_factory")
