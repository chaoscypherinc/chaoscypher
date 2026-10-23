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

from chaoscypher_core.cache.call_site import call_site
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    ATTESTED_NAMES_KEY,
    GIVEN_ALIASES_KEY,
    MERGED_NAMES_KEY,
    node_description,
)


logger = structlog.get_logger(__name__)


# Default from SearchSettings.max_embedding_text_length
MAX_EMBEDDING_TEXT_LENGTH = 16000


NODE_PROVENANCE_KEYS = frozenset(
    {"source_document_id", "source_document_name", "ingested_at", "source_type"}
)
"""Where a source's reading of a node came from: the document, when and how, as the commit stamps it.

Provenance, not content: two sources that wrote the same thing differ here,
and a fold keeps the kept reading's alone (``resolution.apply``).
"""

NODE_EMBED_META_KEYS = NODE_PROVENANCE_KEYS | {"type"}
"""Properties the commit and the importers add to a node; extraction never saw them."""

_NODE_TEXT_FIELDS = frozenset(
    {
        "aliases",
        ATTESTED_NAMES_KEY,
        GIVEN_ALIASES_KEY,
        MERGED_NAMES_KEY,
        "definition",
        "description",
        "content",
    }
)
"""Properties that are the entity's aliases (``aliases_given`` is which of them were given,
``merged_names`` which a merge folded in, ``attested_names`` which the corpus writes) or
description, not its ``properties``."""


def node_embedding_text(node: Any, max_length: int | None = None) -> str:
    """The embedding text of a stored node, built the way its extracted entity's was.

    Extraction embeds the entity dict (name, aliases, description and the P|
    properties) with :func:`entity_to_embedding_text` and the commit stores
    that vector. Every path that re-embeds a stored node uses this function so
    the vector lands in the same place: the search index rebuild, a package
    import, the benchmark's re-index. The commit keeps the description under
    ``properties["definition"]`` (a package may carry ``description``, a
    template ``content``), the aliases under ``properties["aliases"]``, and
    adds bookkeeping (:data:`NODE_EMBED_META_KEYS`) that is left out.

    Until 2026-10-04 there were four texts: the rebuild embedded the label and
    every string property (no aliases, bookkeeping included), the benchmark
    the label, aliases and definition (no properties), a package import the
    definition twice.
    """
    props = getattr(node, "properties", None) or {}
    entity: dict[str, Any] = {"name": getattr(node, "label", "") or ""}
    aliases = props.get("aliases")
    if isinstance(aliases, list) and aliases:
        entity["aliases"] = [str(a) for a in aliases]
    description = node_description(props)
    # the commit's placeholder for an entity extracted without a description
    if isinstance(description, str) and description and not description.startswith("Entity: "):
        entity["description"] = description
    # A property that only repeats the name is the commit filling a template's
    # required title; extraction never embedded it.
    extra = {
        k: v
        for k, v in props.items()
        if k not in NODE_EMBED_META_KEYS and k not in _NODE_TEXT_FIELDS and v != entity["name"]
    }
    if extra:
        entity["properties"] = extra
    return entity_to_embedding_text(entity, max_length)


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

    with call_site("dedup"):
        result = await embedding_service.batch_embed(entity_texts)
    embeddings = result.embeddings

    embeddings_normalized = l2_normalize_embeddings(embeddings)

    return embeddings, embeddings_normalized
