# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Vector shaping shared by every embedding provider and the vector index.

Two rules, one place:

- **Unit length.** Every vector the vector index stores or is queried with is
  scaled to unit L2 norm (:func:`normalize_vector`), so the index's ranking
  (cosine) and any L2 ranking agree, and a similarity is a plain dot product.
- **MRL truncation is explicit.** A Matryoshka model's vector may be cut to
  fewer dimensions than it was trained with (``search.vector_dimensions``),
  but a cut vector is no longer unit length: :func:`fit_dimensions` cuts *and*
  re-normalises, and refuses to pad a vector that is shorter than asked. Until
  2026-10-06 the Ollama provider cut qwen3-embedding:4b's 2560 dimensions to
  1024 silently and stored the stub un-normalised, while the benchmark's naive
  rung embedded the same text at 2560 (audit R1/R7).

Every provider returns its vectors through :func:`fit_provider_vector` (the
OpenAI and Gemini providers passed the API's vectors through as returned
until the wave-2 audit; a guard test runs every provider the factory builds).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from chaoscypher_core.exceptions import LLMError


def normalize_vector(vector: Sequence[float]) -> list[float]:
    """``vector`` scaled to unit L2 norm (a zero vector is returned unchanged).

    Args:
        vector: Any embedding vector.

    Returns:
        A new list of floats whose L2 norm is 1.0 (or all zeros).
    """
    norm = math.sqrt(math.fsum(float(v) * float(v) for v in vector))
    if norm == 0.0:
        return [float(v) for v in vector]
    return [float(v) / norm for v in vector]


def fit_dimensions(vector: Sequence[float], dimensions: int) -> list[float]:
    """``vector`` cut to its first ``dimensions`` values (MRL) and re-normalised to unit length.

    Args:
        vector: A model's raw embedding.
        dimensions: The dimensions wanted; at most ``len(vector)``.

    Returns:
        The unit-length vector of exactly ``dimensions`` values.

    Raises:
        ValueError: When the model's vector is shorter than ``dimensions``
            (padding would invent values the index then ranks on).
    """
    if dimensions < 1:
        msg = f"dimensions must be at least 1, got {dimensions}"
        raise ValueError(msg)
    if len(vector) < dimensions:
        msg = (
            f"the embedding model returned {len(vector)} dimensions, fewer than the "
            f"{dimensions} configured (search.vector_dimensions); set it to the model's "
            f"native size or below"
        )
        raise ValueError(msg)
    return normalize_vector(vector[:dimensions])


def fit_provider_vector(
    vector: Sequence[float], dimensions: int, *, provider: str, model: str
) -> list[float]:
    """A provider's raw vector at ``dimensions`` and unit length (:func:`fit_dimensions`).

    The one exit every embedding provider's vectors take.

    Args:
        vector: The model's raw embedding.
        dimensions: The provider's ``vector_dimensions``.
        provider: The provider type, for the error.
        model: The model name, for the error.

    Returns:
        The unit-length vector of exactly ``dimensions`` values.

    Raises:
        LLMError: When the model returned fewer dimensions than configured.
    """
    try:
        return fit_dimensions([float(v) for v in vector], dimensions)
    except ValueError as exc:
        msg = f"{provider} embedding model {model!r}: {exc}"
        raise LLMError(msg) from exc


__all__ = ["fit_dimensions", "fit_provider_vector", "normalize_vector"]
