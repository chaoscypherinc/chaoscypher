# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Embedding generation and vector operations for entity deduplication.

Converts entities to text representations suitable for embedding models
and generates embeddings via EmbeddingProviderProtocol. Handles vector
normalization for cosine similarity computation.

SRP: Single responsibility for embedding text conversion and generation.
"""

from typing import Any

import numpy as np
import structlog


logger = structlog.get_logger(__name__)


# Default from SearchSettings.max_embedding_text_length
MAX_EMBEDDING_TEXT_LENGTH = 16000


def entity_to_embedding_text(entity: dict[str, Any], max_length: int | None = None) -> str:
    """Convert entity to text suitable for embeddings.

    Includes aliases to enable alias-aware semantic matching.
    Truncates long descriptions to prevent embedding failures
    with models that have context limits.

    Args:
        entity: Entity dictionary.
        max_length: Maximum character length (default: MAX_EMBEDDING_TEXT_LENGTH).

    Returns:
        Text representation of entity (name | aliases | truncated description).

    """
    if max_length is None:
        max_length = MAX_EMBEDDING_TEXT_LENGTH

    parts = []

    # Add name or label (always include, never truncate)
    name = entity.get("name") or entity.get("label")
    if name:
        parts.append(name)

    # Add aliases for better semantic matching
    aliases = entity.get("aliases", [])
    if aliases:
        parts.append(f"Also known as: {', '.join(aliases)}")

    # Calculate remaining space for description and properties
    current_text = " | ".join(parts)
    remaining_chars = max_length - len(current_text) - 10  # Reserve space for separators

    # Add description if available (from AI extraction)
    description = entity.get("description", "")
    if description and remaining_chars > 100:
        if len(description) > remaining_chars:
            # Truncate description, try to break at sentence boundary
            truncated = description[: remaining_chars - 3]
            last_period = truncated.rfind(". ")
            if last_period > remaining_chars // 2:
                truncated = truncated[: last_period + 1]
            else:
                truncated = truncated.rstrip() + "..."
            parts.append(truncated)
            remaining_chars = 0  # No room for properties
        else:
            parts.append(description)
            remaining_chars -= len(description)

    # Add property values if there's still room
    if remaining_chars > 50:
        properties = entity.get("properties", {})
        for value in properties.values():
            if isinstance(value, str) and value.strip():
                if len(value) < remaining_chars:
                    parts.append(value)
                    remaining_chars -= len(value) + 3
            elif isinstance(value, (int, float, bool)):
                str_val = str(value)
                if len(str_val) < remaining_chars:
                    parts.append(str_val)
                    remaining_chars -= len(str_val) + 3
            if remaining_chars <= 50:
                break

    return " | ".join(parts)


def l2_normalize_embeddings(
    embeddings: list[list[float]],
) -> np.ndarray[Any, np.dtype[np.floating[Any]]]:
    """L2-normalize embedding vectors for cosine similarity.

    Shared between the live ``generate_entity_embeddings`` path and the
    cache-hit path in ``deduplicate_entities_semantic`` so both produce
    identical normalized matrices from the same raw vectors.
    """
    embeddings_array = np.array(embeddings, dtype=np.float64)
    norms = np.linalg.norm(embeddings_array, axis=1, keepdims=True)
    normalized: np.ndarray[Any, np.dtype[np.floating[Any]]] = embeddings_array / (norms + 1e-8)
    return normalized


async def generate_entity_embeddings(
    entity_texts: list[str],
    embedding_service: Any,
) -> tuple[list[list[float]], np.ndarray[Any, np.dtype[np.floating[Any]]]]:
    """Generate embeddings for entity texts and return normalized vectors.

    Uses the embedding provider for batch embedding with vector normalization
    for cosine similarity computation.

    Args:
        entity_texts: List of text representations to embed.
        embedding_service: Embedding provider implementing EmbeddingProviderProtocol.

    Returns:
        Tuple of (raw_embeddings, normalized_embeddings_array):
            - raw_embeddings: List of embedding vectors as float lists.
            - normalized_embeddings_array: Numpy array of L2-normalized
              embeddings ready for cosine similarity via dot product.

    """
    logger.info("generating_embeddings_for_deduplication", entity_count=len(entity_texts))

    result = await embedding_service.batch_embed(entity_texts)
    embeddings = result.embeddings

    embeddings_normalized = l2_normalize_embeddings(embeddings)

    return embeddings, embeddings_normalized
