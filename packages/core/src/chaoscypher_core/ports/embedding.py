# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Embedding Provider Protocol for chaoscypher-engine.

Defines the Protocol interface that all embedding providers must implement.
This enables the engine to work with multiple embedding backends (Ollama, OpenAI, Gemini)
through a unified interface.

``EmbeddingHealthStatus`` is defined here (not in the adapter layer) because it is
part of the port's vocabulary — the return type of ``check_health`` belongs to the
contract, not to any specific backend implementation.

Architecture:
    - EmbeddingProviderProtocol defines the contract for embedding operations
    - Concrete providers (OllamaEmbeddingProvider, OpenAIEmbeddingProvider, etc.) implement it
    - Consumer code depends on the protocol, not concrete implementations
    - EmbeddingHealthStatus is the canonical health-check return type at the port level

Example:
    from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol

    async def index_document(provider: EmbeddingProviderProtocol, text: str) -> list[float]:
        result = await provider.embed(text)
        return result.embedding

"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


if TYPE_CHECKING:
    from chaoscypher_core.models import BatchEmbedResult, EmbedResult


class EmbeddingHealthStatus(BaseModel):
    """Health status of an embedding provider.

    Returned by embedding providers to indicate whether the provider
    is operational and report diagnostic details.

    This type is part of the port's vocabulary: it is the return type of
    ``EmbeddingProviderProtocol.check_health`` and belongs at the ports layer,
    not in any adapter-specific module.

    Attributes:
        healthy: Whether the provider is operational.
        provider: Provider type identifier (e.g., "ollama", "openai").
        model: Model name currently configured.
        dimensions: Embedding vector dimensions (0 if unknown).
        message: Optional human-readable status message.
        response_time_ms: Optional response time in milliseconds.

    """

    healthy: bool = Field(description="Whether the provider is operational")
    provider: str = Field(description="Provider type identifier")
    model: str = Field(description="Model name currently configured")
    dimensions: int = Field(default=0, description="Embedding vector dimensions (0 if unknown)")
    message: str | None = Field(default=None, description="Human-readable status message")
    response_time_ms: int | None = Field(default=None, description="Response time in milliseconds")

    model_config = ConfigDict(extra="forbid")


@runtime_checkable
class EmbeddingProviderProtocol(Protocol):
    """Interface for embedding generation providers.

    All embedding providers must implement this protocol to provide
    single-text embedding, batch embedding, and health check capabilities.

    Protocol-based design allows any class with matching methods
    to satisfy this interface (structural typing).

    Implementations:
        - OllamaEmbeddingProvider: Local Ollama server
        - OpenAIEmbeddingProvider: OpenAI API
        - GeminiEmbeddingProvider: Google Gemini API
    """

    model_name: str
    """The model identifier used by this provider."""

    @property
    def provider_type(self) -> str:
        """Return the provider type identifier (e.g., 'ollama', 'openai', 'gemini')."""
        ...

    async def embed(self, text: str) -> EmbedResult:
        """Generate an embedding vector for a single text.

        Args:
            text: Input text to embed.

        Returns:
            EmbedResult with embedding vector, provider name, and optional token usage.

        Raises:
            LLMError: If the embedding request fails.

        """
        ...

    async def batch_embed(self, texts: list[str], batch_size: int = 64) -> BatchEmbedResult:
        """Generate embedding vectors for multiple texts.

        Args:
            texts: List of input texts to embed.
            batch_size: Number of texts to process per batch.

        Returns:
            BatchEmbedResult with embedding vectors, total count, failure count,
            and provider name.

        Raises:
            LLMError: If the batch embedding request fails.

        """
        ...

    async def check_health(self) -> EmbeddingHealthStatus:
        """Check the health and availability of the embedding provider.

        Returns:
            EmbeddingHealthStatus with health state, provider info, model details,
            and optional diagnostics.

        """
        ...
