# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Embedding provider factory.

Creates the appropriate embedding provider based on EngineSettings configuration.
Reads ``settings.embedding.provider`` to select the concrete implementation and
wires constructor arguments from the relevant settings groups.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.adapters.embedding.gemini_provider import GeminiEmbeddingProvider
from chaoscypher_core.adapters.embedding.local_provider import LocalEmbeddingProvider
from chaoscypher_core.adapters.embedding.models import ModelValidationResult
from chaoscypher_core.adapters.embedding.ollama_provider import OllamaEmbeddingProvider
from chaoscypher_core.adapters.embedding.openai_provider import OpenAIEmbeddingProvider
from chaoscypher_core.adapters.embedding.registry import get_curated_dimensions


if TYPE_CHECKING:
    from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)

_CLOUD_PROVIDERS = ("openai", "gemini")


def _resolve_ollama_base_url(settings: EngineSettings) -> str:
    """Resolve the Ollama base URL for embedding requests.

    Priority:
    1. ``settings.embedding.api_base`` (explicit embedding-only override)
    2. Matching instance from ``settings.llm.ollama_instances`` (by id)
    3. ``settings.llm.primary_ollama_url`` (first enabled instance)

    Args:
        settings: Full engine settings.

    Returns:
        Ollama server base URL string.

    """
    if settings.embedding.api_base:
        return settings.embedding.api_base

    instance_id = settings.embedding.ollama_instance_id
    for instance in settings.llm.ollama_instances:
        if instance.id == instance_id and instance.base_url:
            return instance.base_url

    return settings.llm.primary_ollama_url


def create_embedding_provider(settings: EngineSettings) -> EmbeddingProviderProtocol:
    """Create an embedding provider from engine settings.

    Reads ``settings.embedding.provider`` and constructs the matching
    concrete provider with arguments drawn from the appropriate settings
    groups (embedding, search, paths, llm).

    Args:
        settings: Full engine settings.

    Returns:
        Configured embedding provider implementing EmbeddingProviderProtocol.

    Raises:
        ValueError: If the provider name is unknown or a required API key
            is missing for a cloud provider.

    """
    provider = settings.embedding.provider
    model_name = settings.embedding.model
    vector_dimensions = settings.search.vector_dimensions

    if provider == "local":
        cache_dir = Path(settings.paths.data_dir) / "models" / "embeddings"
        logger.info(
            "embedding_provider_created",
            provider=provider,
            model=model_name,
            dimensions=vector_dimensions,
        )
        return LocalEmbeddingProvider(
            model_name=model_name,
            vector_dimensions=vector_dimensions,
            cache_dir=cache_dir,
        )

    if provider == "ollama":
        base_url = _resolve_ollama_base_url(settings)
        logger.info(
            "embedding_provider_created",
            provider=provider,
            model=model_name,
            dimensions=vector_dimensions,
            base_url=base_url,
        )
        return OllamaEmbeddingProvider(
            model_name=model_name,
            vector_dimensions=vector_dimensions,
            base_url=base_url,
        )

    if provider in _CLOUD_PROVIDERS:
        if not settings.embedding.api_key:
            msg = f"API key required for {provider} embedding provider"
            raise ValueError(msg)
        api_key = settings.embedding.api_key.get_secret_value()

        api_base = settings.embedding.api_base

        logger.info(
            "embedding_provider_created",
            provider=provider,
            model=model_name,
            dimensions=vector_dimensions,
        )

        if provider == "openai":
            return OpenAIEmbeddingProvider(
                model_name=model_name,
                vector_dimensions=vector_dimensions,
                api_key=api_key,
                api_base=api_base,
            )

        return GeminiEmbeddingProvider(
            model_name=model_name,
            vector_dimensions=vector_dimensions,
            api_key=api_key,
            api_base=api_base,
        )

    msg = f"Unknown embedding provider: {provider}"
    raise ValueError(msg)


def validate_embedding_model(
    provider_type: str, model_name: str, settings: EngineSettings
) -> ModelValidationResult:
    """Validate that a model name is valid for the given provider.

    Checks the curated model registry for known dimensions. Unknown models
    are accepted with ``native_dimensions=None`` since runtime validation
    will catch truly invalid names when the provider is actually used.

    Args:
        provider_type: Provider identifier ("local", "ollama", "openai", "gemini").
        model_name: Model name to validate.
        settings: Engine settings (reserved for future provider-specific checks).

    Returns:
        ModelValidationResult indicating validity and known dimensions.

    """
    _ = settings  # Reserved for future provider-specific validation

    native_dimensions = get_curated_dimensions(model_name)

    if native_dimensions is not None:
        return ModelValidationResult(
            valid=True,
            model=model_name,
            native_dimensions=native_dimensions,
        )

    # Not in curated registry -- accept for now, runtime will validate
    return ModelValidationResult(
        valid=True,
        model=model_name,
        native_dimensions=None,
    )


__all__ = [
    "create_embedding_provider",
    "validate_embedding_model",
]
