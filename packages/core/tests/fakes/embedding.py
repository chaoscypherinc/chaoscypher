# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""FakeEmbeddingProvider — deterministic stand-in for test pipelines.

Implements the public surface of ``EmbeddingProviderProtocol`` with
embeddings derived from the input text's hash so the same text always
maps to the same vector. No network, no model load, no randomness across
runs.

Used by the pipeline mocked-tests suite to surface contract-drift bugs
between handlers that the real provider would only catch in a
Docker-driven e2e run.
"""

from __future__ import annotations

import numpy as np

from chaoscypher_core.models import BatchEmbedResult, EmbedResult, TokenUsage


__all__ = ["FakeEmbeddingProvider"]


class FakeEmbeddingProvider:
    """Deterministic embedding provider for in-process pipeline tests.

    Matches the structural shape of ``EmbeddingProviderProtocol``:

    - ``model_name`` attribute
    - ``provider_type`` property
    - ``async embed(text)`` returning ``EmbedResult``
    - ``async batch_embed(texts, batch_size=64)`` returning ``BatchEmbedResult``

    Vectors are seeded from ``hash(text) & 0xFFFFFFFF`` so identical
    inputs always produce identical outputs. Output values are Python
    ``list[float]`` to match the real provider's Pydantic-validated
    contract (``BatchEmbedResult.embeddings: list[list[float]]``).

    Attributes:
        model_name: Fixed identifier used by tests asserting on the
            persisted ``embedding_model`` column.
        dimensions: Output vector length (default 384 = production
            all-MiniLM-L6-v2; override for dimension-mismatch tests).
        call_count: Per-instance counter so tests can assert how many
            times ``batch_embed`` / ``embed`` were invoked.
    """

    model_name: str = "fake-embed-test"

    def __init__(self, dimensions: int = 384) -> None:
        """Build a deterministic fake provider.

        Args:
            dimensions: Vector length. Defaults to the production
                all-MiniLM-L6-v2 dimension.
        """
        self.dimensions = dimensions
        self.call_count = 0

    @property
    def provider_type(self) -> str:
        """Return the provider type identifier (matches the protocol)."""
        return "fake"

    async def embed(self, text: str) -> EmbedResult:
        """Embed a single text deterministically."""
        self.call_count += 1
        return EmbedResult(
            embedding=self._deterministic_vector(text),
            provider=self.provider_type,
            usage=TokenUsage(
                input_tokens=max(1, len(text) // 4),
                output_tokens=0,
                total_tokens=max(1, len(text) // 4),
            ),
        )

    async def batch_embed(self, texts: list[str], batch_size: int = 64) -> BatchEmbedResult:
        """Embed a list of texts; each text → same vector across runs."""
        self.call_count += 1
        embeddings = [self._deterministic_vector(t) for t in texts]
        return BatchEmbedResult(
            embeddings=embeddings,
            total=len(embeddings),
            failed=0,
            provider=self.provider_type,
        )

    def _deterministic_vector(self, text: str) -> list[float]:
        """Build a stable vector for ``text``.

        Uses numpy for clean RNG semantics, then converts to ``list[float]``
        to match the Pydantic-typed wire shape consumers expect (the real
        local provider does the same cast — see
        ``adapters/embedding/local_provider.py``).
        """
        seed = hash(text) & 0xFFFFFFFF
        rng = np.random.default_rng(seed)
        vector: list[float] = rng.standard_normal(self.dimensions).astype(np.float32).tolist()
        return vector
