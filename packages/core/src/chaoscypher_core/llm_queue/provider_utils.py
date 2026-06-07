# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM Provider Configuration Utilities.

Consolidates provider-specific configuration lookups (context windows, model names)
to avoid duplicated provider switching logic across the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


@dataclass
class ProviderConfig:
    """Configuration for an LLM provider.

    Attributes:
        provider: Provider name (ollama, openai, anthropic, gemini)
        model: Model name string
        context_window: Context window size in tokens

    """

    provider: str
    model: str
    context_window: int


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings


def get_provider_config(settings: Settings) -> ProviderConfig:
    """Get provider-specific configuration from settings.

    Centralizes the provider switching logic that was duplicated across
    get_context_window_for_provider() and get_model_name().

    Args:
        settings: Application settings with LLM configuration

    Returns:
        ProviderConfig with provider name, model, and context window

    """
    provider = settings.llm.chat_provider.lower()

    configs = {
        "ollama": {
            "context_window": settings.llm.ollama_num_ctx or 32768,
            "model": settings.llm.ollama_chat_model,
        },
        "openai": {
            "context_window": settings.llm.openai_context_window or 128000,
            "model": settings.llm.openai_chat_model,
        },
        "anthropic": {
            "context_window": settings.llm.anthropic_context_window or 200000,
            "model": settings.llm.anthropic_chat_model,
        },
        "gemini": {
            "context_window": settings.llm.gemini_context_window or 1048576,
            "model": settings.llm.gemini_chat_model,
        },
    }

    config = configs.get(provider, {"context_window": 32768, "model": "unknown"})

    return ProviderConfig(
        provider=provider,
        model=config["model"],
        context_window=config["context_window"],
    )


__all__ = [
    "ProviderConfig",
    "get_provider_config",
]
