# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cloud Model Registry - LLM model specs and pricing data.

Provides access to cloud LLM model specifications including context windows,
output limits, and pricing information. Lives in adapters/llm/ because it
contains provider-specific infrastructure data (model catalogs, pricing).
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog


logger = structlog.get_logger(__name__)


# Path to the cloud models JSON file
# From adapters/llm/model_registry.py -> chaoscypher_core/data/cloud_models.json
_MODELS_FILE = Path(__file__).parent.parent.parent / "data" / "cloud_models.json"


@lru_cache(maxsize=1)
def _load_models_data() -> dict[str, Any]:
    """Load and cache the cloud models JSON data."""
    if not _MODELS_FILE.exists():
        logger.warning("cloud_models_file_not_found", path=str(_MODELS_FILE))
        return {}

    with open(_MODELS_FILE) as f:
        data: dict[str, Any] = json.load(f)
        return data


class CloudModelRegistry:
    """Registry for cloud LLM model specifications.

    Provides access to model metadata including context windows, output limits,
    and pricing for cloud providers (Gemini, OpenAI, Anthropic).

    Example:
        >>> registry = CloudModelRegistry()
        >>> models = registry.get_models("gemini")
        >>> for model in models:
        ...     print(f"{model['display_name']}: {model['context_window']} tokens")
        >>>
        >>> # Get specific model
        >>> model = registry.get_model("openai", "gpt-4.1")
        >>> if model:
        ...     print(f"Context: {model['context_window']}")
        ...     print(f"Price: ${model['pricing']['input_per_million']}/M input")

    """

    def __init__(self) -> None:
        """Initialize the model registry."""
        self._data = _load_models_data()

    def get_providers(self) -> list[str]:
        """Get list of available cloud providers.

        Returns:
            List of provider IDs (e.g., ['gemini', 'openai', 'anthropic'])

        """
        return list(self._data.keys())

    def get_provider_info(self, provider: str) -> dict[str, Any] | None:
        """Get provider metadata.

        Args:
            provider: Provider ID (gemini, openai, anthropic)

        Returns:
            Provider info dict with display_name and models, or None if not found

        """
        return self._data.get(provider)

    def get_models(self, provider: str) -> list[dict[str, Any]]:
        """Get all models for a provider.

        Args:
            provider: Provider ID (gemini, openai, anthropic)

        Returns:
            List of model dictionaries with specs and pricing

        """
        provider_data = self._data.get(provider)
        if not provider_data:
            return []
        models: list[dict[str, Any]] = provider_data.get("models", [])
        return models


# Singleton instance
_registry: CloudModelRegistry | None = None


def get_model_registry() -> CloudModelRegistry:
    """Get the singleton model registry instance.

    Returns:
        CloudModelRegistry instance

    """
    global _registry
    if _registry is None:
        _registry = CloudModelRegistry()
    return _registry


__all__ = [
    "CloudModelRegistry",
    "get_model_registry",
]
