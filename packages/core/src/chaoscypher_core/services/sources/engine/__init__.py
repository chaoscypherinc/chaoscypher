# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source processing Execution Engine.

Document processing pipeline execution.

Components:
- ExtractionService: Entity extraction from documents
- SourceCommitService: Commits extracted entities to graph
- EntityProcessor: Deduplication and entity processing

Example:
    from chaoscypher_core.services.sources.engine import ExtractionService

    # Prefer Engine.extraction_service (auto-wires embedding_service) when
    # an Engine is available. Constructing directly requires passing the
    # embedding_service kwarg explicitly so semantic dedup runs.
    extractor = ExtractionService(
        graph_repository=graph_repo,
        llm_provider=llm_provider,
        settings=settings,
        embedding_service=embedding_service,
    )

"""

from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService
from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor
from chaoscypher_core.services.sources.engine.extraction.service import ExtractionService


__all__ = [
    "EntityProcessor",
    "ExtractionService",
    "SourceCommitService",
]
