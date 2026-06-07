# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Embedding provider models for chaoscypher-engine.

Pydantic models for embedding provider model validation and curated/cloud
model definitions used by the embedding abstraction layer.

Note: ``EmbeddingHealthStatus`` used to be defined here and shimmed from
``chaoscypher_core.ports.embedding``.  It now lives only at the port,
matching the hex-arch direction (adapters depend on ports, not the other
way around).  Callers should import it from ``chaoscypher_core.ports.embedding``
or the ``chaoscypher_core.adapters.embedding`` barrel.
"""

from pydantic import BaseModel, ConfigDict, Field


__all__ = [
    "CloudModel",
    "CuratedModel",
    "ModelValidationResult",
]


class ModelValidationResult(BaseModel):
    """Result of validating an embedding model with a provider.

    Used to verify that a model is available and report its native dimensions.

    Attributes:
        valid: Whether the model is valid and available.
        model: Model name that was validated.
        native_dimensions: Native output dimensions of the model, if known.
        error: Error message if validation failed.

    """

    valid: bool = Field(description="Whether the model is valid and available")
    model: str = Field(description="Model name that was validated")
    native_dimensions: int | None = Field(
        default=None, description="Native output dimensions of the model"
    )
    error: str | None = Field(default=None, description="Error message if validation failed")

    model_config = ConfigDict(extra="forbid")


class CuratedModel(BaseModel):
    """A curated local embedding model with known characteristics.

    Represents a vetted embedding model available through Ollama with
    pre-defined dimensions and Matryoshka Representation Learning (MRL) support.

    Attributes:
        name: Human-readable model name.
        local: Local model identifier (e.g., "nomic-embed-text").
        ollama: Ollama-specific model tag (e.g., "nomic-embed-text:latest").
        dimensions: Native embedding dimensions.
        mrl: Whether the model supports Matryoshka Representation Learning.
        default: Whether this is the default model.

    """

    name: str = Field(description="Human-readable model name")
    local: str = Field(description="Local model identifier")
    ollama: str = Field(description="Ollama-specific model tag")
    dimensions: int = Field(description="Native embedding dimensions")
    mrl: bool = Field(description="Whether the model supports Matryoshka Representation Learning")
    default: bool = Field(default=False, description="Whether this is the default model")

    model_config = ConfigDict(extra="forbid")


class CloudModel(BaseModel):
    """A cloud embedding model definition.

    Represents an embedding model available through a cloud provider
    (e.g., OpenAI, Gemini) with known dimensions and capabilities.

    Attributes:
        name: Human-readable model name.
        model: Provider-specific model identifier.
        dimensions: Native embedding dimensions.
        mrl: Whether the model supports Matryoshka Representation Learning.
        current: Whether this model is currently available from the provider.

    """

    name: str = Field(description="Human-readable model name")
    model: str = Field(description="Provider-specific model identifier")
    dimensions: int = Field(description="Native embedding dimensions")
    mrl: bool = Field(description="Whether the model supports Matryoshka Representation Learning")
    current: bool = Field(default=True, description="Whether this model is currently available")

    model_config = ConfigDict(extra="forbid")
