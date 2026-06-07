# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Extraction Operations - chunk-based distributed extraction on the LLM queue.

Provides handlers for distributed chunk extraction:
- extract_chunk: Extract entities/relationships from a single chunk
- finalize_extraction: Aggregate results, deduplicate, match templates

Sub-modules:
- chunk_extraction_service: Main service class with queue and handler methods
- extraction_metrics_service: LLM metrics persistence
- extraction_finalizer: Results aggregation, deduplication, and commit queuing

Example:
    from chaoscypher_core.operations.extraction import (
        ChunkExtractionOperationsService,
    )

    service = ChunkExtractionOperationsService(
        graph_repository=repo,
        config_manager=config,
        llm_service=llm,
        source_repository=adapter,
    )
    service.register_handlers()

"""

from chaoscypher_core.operations.extraction.chunk_extraction_service import (
    ChunkExtractionOperationsService,
)
from chaoscypher_core.operations.extraction.schemas import (
    RawEntity,
    RawRelationship,
    validate_raw_entities,
    validate_raw_relationships,
)


__all__ = [
    "ChunkExtractionOperationsService",
    "RawEntity",
    "RawRelationship",
    "validate_raw_entities",
    "validate_raw_relationships",
]
