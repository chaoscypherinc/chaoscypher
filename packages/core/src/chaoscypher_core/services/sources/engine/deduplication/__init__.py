# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Entity Deduplication Service.

Provides exact and semantic entity deduplication for knowledge graph operations.
Prevents duplicate entities during document source processing by comparing extracted
entities against existing graph nodes using label matching and vector similarity.

Components:
    - EntityProcessor: Main deduplication processor with exact and semantic matching
    - embedding_generator: Embedding text conversion and vector generation
    - similarity_matcher: Name normalization, alias matching, and merge decisions

The deduplication pipeline:
1. Extract entities from document chunks
2. Check for exact label matches in existing nodes
3. Perform semantic similarity search for near-duplicates
4. Merge or create entities based on confidence thresholds

Example:
    from chaoscypher_core.services.sources.engine.deduplication import EntityProcessor

    processor = EntityProcessor(title_words=frozenset({"mr", "dr", "sir"}))
    deduplicated, mapping = processor.deduplicate_entities_with_mapping(
        entities=extracted_entities,
    )
"""

from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor
from chaoscypher_core.services.sources.engine.deduplication.similarity_matcher import (
    are_types_compatible,
    normalize_compatibility_map,
)


__all__ = ["EntityProcessor", "are_types_compatible", "normalize_compatibility_map"]
