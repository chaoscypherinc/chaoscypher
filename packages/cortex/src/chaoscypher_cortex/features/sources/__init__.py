# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Sources Feature.

Permanent document storage with chunk, citation, and tag management.

This feature provides persistent storage for committed documents after successful
import and extraction. Manages the document lifecycle including source metadata,
hierarchical chunks, citation tracking, and flexible tagging. Distinct from
imports (transient) - sources represent the final committed knowledge base.
Follows VSA architecture with dual routers for sources and tags.

Components:
- SourceService: Business logic for document and chunk operations
- TagService: Business logic for tag CRUD and source-tag assignment
- sources_router: FastAPI endpoints for /api/v1/sources
- tags_router: FastAPI endpoints for /api/v1/tags

Architecture:
Standard VSA pattern using SqliteAdapter (implements SourceStorageProtocol) for
all SQLModel persistence operations. Service layer handles document lifecycle,
chunk hierarchy, citation resolution, and tag organization. Supports tag-based
filtering and full-text search integration.

Example:
    from chaoscypher_cortex.features.sources import SourceService
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

    # Query committed documents
    adapter = SqliteAdapter(db_path="/path/to/app.db")
    adapter.connect()
    service = SourceService(adapter, settings)
    sources = service.list_sources(tags=["research", "ai"])
    chunks = service.get_source_chunks(source_id, include_citations=True)
    adapter.disconnect()

"""

from chaoscypher_cortex.features.sources.api import router as sources_router
from chaoscypher_cortex.features.sources.chunks_api import router as chunks_router
from chaoscypher_cortex.features.sources.extraction_api import router as extraction_router
from chaoscypher_cortex.features.sources.progress import SourceProgress, map_status_to_progress
from chaoscypher_cortex.features.sources.service import SourceService
from chaoscypher_cortex.features.sources.tag_service import TagService
from chaoscypher_cortex.features.sources.tags_api import router as tags_router
from chaoscypher_cortex.features.sources.upload_service import UploadService
from chaoscypher_cortex.features.sources.vision_pages_api import (
    router as vision_pages_router,
)


__all__ = [
    "SourceProgress",
    "SourceService",
    "TagService",
    "UploadService",
    "chunks_router",
    "extraction_router",
    "map_status_to_progress",
    "sources_router",
    "tags_router",
    "vision_pages_router",
]
