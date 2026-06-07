# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Processing Feature.

Document processing and entity extraction pipeline.

This feature orchestrates the complete document processing pipeline from file upload
through RAG indexing to optional entity extraction. Implements RAG-first architecture
with two-stage processing: automatic indexing (30s) and optional extraction (5min).
Supports multiple file formats via pluggable loaders and template-based entity
matching. All business logic follows SRP with specialized services in chaoscypher.

Components:
- SourceFileValidators: File validation and format detection
- LoaderRegistry: Pluggable file format loaders (PDF, DOCX, TXT, etc.)
Architecture:
Backend uses SqliteAdapter (implements SourceFileStorageProtocol) for source_processing tracking.
Core orchestration and extraction logic lives in engine/services/source_processing for
CLI reusability. Uses storage adapter pattern for framework-agnostic persistence.

Example:
    from chaoscypher_core.operations.sources.processing import SourceFileValidators

    # Validate a file before processing
    validators = SourceFileValidators(source_manager, llm_provider, database_name)
    validators.validate_file_for_analysis(source_id)

"""

from chaoscypher_core.operations.sources.processing.validators import SourceFileValidators
from chaoscypher_core.services.sources.loaders import LoaderRegistry
from chaoscypher_core.services.sources.models.entities import (
    Entity,
    Relationship,
    SuggestedTemplate,
)


__all__ = [
    "Entity",
    "LoaderRegistry",
    "Relationship",
    "SourceFileValidators",
    "SuggestedTemplate",
]
