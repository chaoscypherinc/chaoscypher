# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Search Services - Full-Text and Vector Search.

Provides search and indexing services for knowledge graphs.

Architecture:
- engine/: All search execution operations (no management/CRUD layer)

Components:
- SearchService: Full-text and vector search operations
- IndexingService: Document indexing and embedding generation
- TopicResearcher: Research and analysis capabilities

Example:
    from chaoscypher_core.services.search import SearchService, IndexingService

    # Search operations
    search_service = SearchService(
        search_repo, graph_repo, indexing_repo, source_repo, settings=settings,
    )
    results = search_service.keyword_search("query")

    # Indexing operations
    indexing_service = IndexingService(indexing_repo, settings)
    await indexing_service.create_index(source_id)

"""

# Engine: All execution (no management layer)
from chaoscypher_core.services.search.engine import (
    IndexingService,
    SearchService,
    TopicResearcher,
)


__all__ = [
    "IndexingService",
    "SearchService",
    "TopicResearcher",
]
