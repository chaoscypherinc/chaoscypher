# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source processing Commit Service Package - Graph Persistence.

Handles the final commit phase of document source processing, persisting extracted
entities, relationships, and templates to the knowledge graph. Each handler
is responsible for a specific aspect of the commit process.

Components:
    - SourceCommitService: Main orchestration service
    - EntityTemplateMatcher: Match entities to existing templates
    - TemplateCommitHandler: Create new templates during commit
    - EntityCommitHandler: Create entity nodes with source tracking
    - RelationshipCommitHandler: Create relationship edges

The commit pipeline:
1. Match entities to templates (or create new ones)
2. Create/update template definitions
3. Create entity nodes with properties and source links
4. Create relationship edges between entities
5. Update search indices

Example:
    from chaoscypher_core.services.sources.engine.commit import SourceCommitService

    commit_service = SourceCommitService(
        graph_repository=graph_repo,
        source_repository=source_repo,
        sources_repository=sources_repo,
        indexing_repository=indexing_repo,
        search_repository=search_repo,
        settings=settings,
    )
    result = await commit_service.commit(
        file_id="source_001",
        commit_data=extraction_results,
        file_info={"filename": "paper.pdf"},
    )
"""

from chaoscypher_core.services.sources.engine.commit.entity import EntityCommitHandler
from chaoscypher_core.services.sources.engine.commit.matcher import EntityTemplateMatcher
from chaoscypher_core.services.sources.engine.commit.relation import RelationshipCommitHandler
from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService
from chaoscypher_core.services.sources.engine.commit.template import TemplateCommitHandler


__all__ = [
    "EntityCommitHandler",
    "EntityTemplateMatcher",
    "RelationshipCommitHandler",
    "SourceCommitService",
    "TemplateCommitHandler",
]
