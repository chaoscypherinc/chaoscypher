# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Curated embedding model registry.

Defines vetted embedding models with known characteristics for local (CPU),
Ollama, and cloud providers. The registry enables model resolution by
display name or provider-specific ID, and provides dimension lookups
for automatic vector index configuration.
"""

from chaoscypher_core.adapters.embedding.models import CloudModel, CuratedModel


# ============================================================================
# Curated Local / Ollama Models
# ============================================================================

CURATED_EMBEDDING_MODELS: list[CuratedModel] = [
    CuratedModel(
        name="Qwen3 Embedding 0.6B",
        local="Qwen/Qwen3-Embedding-0.6B",
        ollama="qwen3-embedding:0.6b",
        dimensions=1024,
        mrl=True,
        default=True,
    ),
    CuratedModel(
        name="Qwen3 Embedding 4B",
        local="Qwen/Qwen3-Embedding-4B",
        ollama="qwen3-embedding:4b",
        dimensions=2560,
        mrl=True,
    ),
    CuratedModel(
        name="Qwen3 Embedding 8B",
        local="Qwen/Qwen3-Embedding-8B",
        ollama="qwen3-embedding:8b",
        dimensions=4096,
        mrl=True,
    ),
    CuratedModel(
        name="BGE-M3",
        local="BAAI/bge-m3",
        ollama="bge-m3",
        dimensions=1024,
        # BGE-M3 doesn't ship MRL: dimension is fixed at 1024.
        mrl=False,
    ),
    CuratedModel(
        name="EmbeddingGemma 300M",
        local="google/embeddinggemma-300m",
        ollama="embeddinggemma",
        dimensions=768,
        mrl=True,
    ),
]

# ============================================================================
# Cloud Provider Models
# ============================================================================

CLOUD_EMBEDDING_MODELS: dict[str, list[CloudModel]] = {
    "openai": [
        CloudModel(
            name="Text Embedding 3 Large",
            model="text-embedding-3-large",
            dimensions=3072,
            mrl=True,
            current=True,
        ),
        CloudModel(
            name="Text Embedding 3 Small",
            model="text-embedding-3-small",
            dimensions=1536,
            mrl=True,
            current=False,
        ),
    ],
    "gemini": [
        CloudModel(
            # Multimodal (text + image + audio + video). Native 3072d, MRL-truncatable
            # to 768 / 1536 / 3072. Released Nov 2025 as the successor to
            # `gemini-embedding-001` for callers that want a single embedding space
            # across modalities.
            name="Gemini Embedding 2 Preview",
            model="gemini-embedding-2-preview",
            dimensions=3072,
            mrl=True,
            current=True,
        ),
        CloudModel(
            # Text-only flagship, generally available. Default 3072d with MRL.
            name="Gemini Embedding 001",
            model="gemini-embedding-001",
            dimensions=3072,
            mrl=True,
            current=False,
        ),
    ],
}


# ============================================================================
# Helper Functions
# ============================================================================


def get_default_model() -> CuratedModel:
    """Return the default curated embedding model.

    Returns:
        The CuratedModel instance marked as default.

    Raises:
        ValueError: If no default model is defined in the registry.

    """
    for model in CURATED_EMBEDDING_MODELS:
        if model.default:
            return model
    msg = "No default embedding model defined in CURATED_EMBEDDING_MODELS"
    raise ValueError(msg)


def resolve_model_name(display_or_id: str, provider: str) -> str | None:
    """Resolve a display name or model ID to the provider-specific model name.

    Searches curated models (for local/ollama providers) and cloud models
    (for openai/gemini providers) by display name, local ID, ollama tag,
    or cloud model ID.

    Args:
        display_or_id: Human-readable name or provider-specific model identifier.
        provider: Target provider ("local", "ollama", "openai", "gemini").

    Returns:
        Provider-specific model name string, or None if not found in the registry.

    """
    # Search curated models for local/ollama providers
    if provider in ("local", "ollama"):
        for model in CURATED_EMBEDDING_MODELS:
            if display_or_id in (model.name, model.local, model.ollama):
                return model.local if provider == "local" else model.ollama
        return None

    # Search cloud models for cloud providers
    cloud_models = CLOUD_EMBEDDING_MODELS.get(provider, [])
    for cloud_model in cloud_models:
        if display_or_id in (cloud_model.name, cloud_model.model):
            return cloud_model.model
    return None


def get_curated_dimensions(model_id: str) -> int | None:
    """Look up the native embedding dimensions for a model by any identifier.

    Searches both curated and cloud model registries by local ID, ollama tag,
    cloud model ID, or display name.

    Args:
        model_id: Model identifier (local, ollama, cloud, or display name).

    Returns:
        Native embedding dimensions, or None if the model is not in the registry.

    """
    # Search curated models
    for model in CURATED_EMBEDDING_MODELS:
        if model_id in (model.name, model.local, model.ollama):
            return model.dimensions

    # Search cloud models
    for cloud_models in CLOUD_EMBEDDING_MODELS.values():
        for cloud_model in cloud_models:
            if model_id in (cloud_model.name, cloud_model.model):
                return cloud_model.dimensions

    return None


__all__ = [
    "CLOUD_EMBEDDING_MODELS",
    "CURATED_EMBEDDING_MODELS",
    "get_curated_dimensions",
    "get_default_model",
    "resolve_model_name",
]
