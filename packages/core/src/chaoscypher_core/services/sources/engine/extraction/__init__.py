# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Extraction Service Package - AI-Powered Entity Extraction.

Orchestrates the entity extraction phase of document source processing. Uses LLM
providers to identify entities, relationships, and properties from document
chunks, then matches them to templates and generates vector embeddings.

Components:
    - ExtractionService: Main extraction pipeline orchestration
    - Orchestration helpers: Shared logic for CLI and Cortex pipelines

Sub-modules:
    - preprocessor: Text/document preprocessing (entity normalization)
    - extractor: Core entity extraction, deduplication, and embeddings
    - template_matcher: Template matching and suggestion generation

The extraction pipeline:
1. Process document chunks through LLM for entity extraction (2-pass per chunk)
2. Match extracted entities to existing templates (or suggest new ones)
3. Generate vector embeddings for semantic search
4. Return structured extraction results

Example:
    from chaoscypher_core.services.sources.engine.extraction import ExtractionService

    # Prefer Engine.extraction_service (auto-wires embedding_service) when
    # an Engine is available. Constructing directly requires passing the
    # embedding_service kwarg explicitly so semantic dedup runs.
    service = ExtractionService(
        graph_repository=graph_repo,
        llm_provider=llm_provider,
        settings=settings,
        embedding_service=embedding_service,
    )
    result = await service.finalize_distributed_extraction(
        raw_entities=entities,
        raw_relationships=relationships,
    )
    # result["entities"], result["relationships"], result["suggested_templates"]
"""

from chaoscypher_core.services.sources.engine.extraction.extractor import (
    extract_entities_from_groups,
    generate_embeddings,
    run_deduplication,
)
from chaoscypher_core.services.sources.engine.extraction.orchestration import (
    FilterStats,
    aggregate_chunk_results,
    apply_depth_strategy,
    build_extraction_groups,
    cache_quality_scores,
    detect_extraction_domain,
    filter_and_strip_chunks,
    format_extraction_templates,
    resolve_content_exclusions,
    strip_chunk_content,
)
from chaoscypher_core.services.sources.engine.extraction.preprocessor import (
    normalize_entities,
)
from chaoscypher_core.services.sources.engine.extraction.service import ExtractionService
from chaoscypher_core.services.sources.engine.extraction.template_matcher import (
    suggest_edge_templates,
)


__all__ = [
    "ExtractionService",
    "FilterStats",
    "aggregate_chunk_results",
    "apply_depth_strategy",
    "build_extraction_groups",
    "cache_quality_scores",
    "detect_extraction_domain",
    "extract_entities_from_groups",
    "filter_and_strip_chunks",
    "format_extraction_templates",
    "generate_embeddings",
    "normalize_entities",
    "resolve_content_exclusions",
    "run_deduplication",
    "strip_chunk_content",
    "suggest_edge_templates",
]
