# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source processing System - Document Processing and Analysis.

Provides document source processing, extraction, and analysis services.

Architecture:
- management/: File CRUD and status management (SourceProcessingService)
- engine/: Source processing execution (ExtractionService, CommitService)
- models/: Domain models (Entity, Relationship, SuggestedTemplate)
- utils/: Shared utilities

Note: Source-row detail/summary dict projections are applied inside the
SQLite adapter's ``source_files.py`` mixin rather than in a services-side
mapper, so SQLModel entities never cross the adapter boundary.

Components:
- SourceProcessingService: CRUD operations for source processing files
- ExtractionService: Entity extraction from documents
- SourceCommitService: Commits extracted entities to graph
- EntityProcessor: Deduplication and entity processing

Example:
    from chaoscypher_core.services.sources import SourceProcessingService

    # Manage source processing files
    service = SourceProcessingService(mgr, ops, config, validators)
    result = await service.upload_file(content, filename)

"""

# Management: File CRUD
# Engine: Execution pipeline
from chaoscypher_core.services.sources.engine import (
    EntityProcessor,
    ExtractionService,
    SourceCommitService,
)

# Heartbeat: long-handler liveness signal for the recovery reconciler
from chaoscypher_core.services.sources.heartbeat import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    SourceHeartbeat,
    source_heartbeat,
)
from chaoscypher_core.services.sources.management import SourceProcessingService

# Models: Domain models
from chaoscypher_core.services.sources.models import (
    Entity,
    Relationship,
    SuggestedTemplate,
)


__all__ = [
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    # Models
    "Entity",
    "EntityProcessor",
    "ExtractionService",
    "Relationship",
    "SourceCommitService",
    # Heartbeat
    "SourceHeartbeat",
    # Management
    "SourceProcessingService",
    "SuggestedTemplate",
    "source_heartbeat",
]
