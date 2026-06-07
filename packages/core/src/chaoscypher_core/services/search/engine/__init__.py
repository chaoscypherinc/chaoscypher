# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Search Execution Engine.

All search execution operations (no CRUD/management layer).

Components:
- SearchService: Full-text and vector search execution
- IndexingService: Document indexing and embedding generation
- TopicResearcher: Research and analysis with web sources
- Query expansion utilities (functions)

Example:
    from chaoscypher_core.services.search.engine import SearchService, IndexingService

    # Execute searches
    search = SearchService(
        search_repo, graph_repo, indexing_repo, source_repo, settings=settings,
    )
    results = search.keyword_search("knowledge graph", limit=10)

    # Index documents
    indexer = IndexingService(indexing_repo, settings)
    await indexer.create_index(source_id)

"""

from chaoscypher_core.services.search.engine.index import IndexingService
from chaoscypher_core.services.search.engine.research import TopicResearcher
from chaoscypher_core.services.search.engine.search import SearchService


__all__ = [
    "IndexingService",
    "SearchService",
    "TopicResearcher",
]
