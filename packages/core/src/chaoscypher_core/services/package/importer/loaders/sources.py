# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Loader - Imports sources, chunks, citations, and tags from CCX packages.

Handles importing RAG content from sources.jsonl files with base64-encoded
embeddings, document chunks, citations, and source tags.

Example:
    from chaoscypher_core.services.package.importer.loaders import SourceLoader

    loader = SourceLoader(sources_repository)
    loader.load(sources_data, mapper, stats, "default")
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.package.importer.loaders.base import PackageLoaderBase
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.services.package.importer.models import IdMapper, ImportStats


logger = structlog.get_logger(__name__)


class SourceLoader(PackageLoaderBase):
    """Loads sources, chunks, citations, and tags from CCX packages.

    Handles importing RAG content from sources.jsonl files. Each line in
    the JSONL file contains a complete source with its chunks, citations,
    and tags nested inside.

    Chunks include base64-encoded embeddings which are decoded back to
    bytes during import.

    Attributes:
        sources_repository: Repository for source/chunk/citation operations.
    """

    def __init__(self, sources_repository: Any) -> None:
        """Initialize source loader.

        Args:
            sources_repository: Repository implementing SourceStorageProtocol.
        """
        self.sources_repository = sources_repository

    def load(
        self,
        data: dict[str, Any] | list[dict[str, Any]],
        mapper: IdMapper,
        stats: ImportStats,
        database_name: str,
    ) -> None:
        """Load sources from parsed sources.jsonl data.

        Args:
            data: List of source dictionaries from parsed JSONL.
            mapper: IdMapper for tracking ID transformations.
            stats: ImportStats for recording statistics.
            database_name: Target database name.
        """
        if not isinstance(data, list):
            stats.errors.append("Invalid sources.jsonl format: expected list")
            return

        logger.info("loading_sources", source_count=len(data))

        for source_data in data:
            self._load_source(source_data, mapper, stats, database_name)

        logger.info(
            "sources_loaded",
            sources_imported=stats.sources_imported,
            chunks_imported=stats.chunks_imported,
            citations_imported=stats.citations_imported,
        )

    def _load_source(
        self,
        source_data: dict[str, Any],
        mapper: IdMapper,
        stats: ImportStats,
        database_name: str,
    ) -> None:
        """Load a single source with its chunks, citations, and tags.

        Args:
            source_data: Source dictionary with nested chunks/citations/tags.
            mapper: IdMapper for tracking ID transformations.
            stats: ImportStats for recording statistics.
            database_name: Target database name.
        """
        original_source_id = source_data.get("id")
        if not original_source_id:
            stats.warnings.append("Skipping source without id")
            return

        # Extract nested data
        chunks_data = source_data.pop("chunks", [])
        citations_data = source_data.pop("citations", [])
        tags_data = source_data.pop("tags", [])

        # Generate new source ID
        new_source_id = generate_id("src")

        try:
            # Create source record
            source_record = {
                "id": new_source_id,
                "database_name": database_name,
                "version": source_data.get("version", 1),
                "parent_id": source_data.get("parent_id"),
                "source_type": source_data.get("source_type", "document"),
                "title": source_data.get("title", "Untitled"),
                "origin_url": source_data.get("origin_url"),
                "chunk_count": len(chunks_data),
                "total_content_length": source_data.get("total_content_length", 0),
                "embedding_model": source_data.get("embedding_model"),
                "embedding_dimensions": source_data.get("embedding_dimensions"),
                "status": source_data.get("status", "active"),
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "user_metadata": source_data.get("metadata", {}),
            }

            self.sources_repository.create_source(source_record)
            mapper.map_source(original_source_id, new_source_id)
            stats.sources_imported += 1

            logger.debug(
                "source_imported",
                original_id=original_source_id,
                new_id=new_source_id,
                title=source_record["title"],
            )

            # Load chunks for this source
            self._load_chunks(chunks_data, new_source_id, mapper, stats, database_name)

            # Load citations for this source
            self._load_citations(citations_data, new_source_id, mapper, stats, database_name)

            # Apply tags to source
            self._apply_tags(tags_data, new_source_id, stats)

        except Exception as e:
            stats.errors.append(f"Failed to import source '{original_source_id}': {e}")
            logger.exception("source_import_failed", source_id=original_source_id)

    def _load_chunks(
        self,
        chunks_data: list[dict[str, Any]],
        source_id: str,
        mapper: IdMapper,
        stats: ImportStats,
        database_name: str,
        system_embedding_model: str | None = None,
        system_embedding_dimensions: int | None = None,
    ) -> None:
        """Load chunks for a source.

        Args:
            chunks_data: List of chunk dictionaries.
            source_id: New source ID to associate chunks with.
            mapper: IdMapper for tracking chunk ID transformations.
            stats: ImportStats for recording statistics.
            database_name: Target database name.
            system_embedding_model: Current system's embedding model name.
            system_embedding_dimensions: Current system's embedding dimensions.
        """
        for chunk_data in chunks_data:
            original_chunk_id = chunk_data.get("id")
            new_chunk_id = generate_id("chunk")

            # Decode base64 embedding back to bytes
            embedding_str = chunk_data.get("embedding")
            embedding_bytes = None
            if embedding_str:
                try:
                    if isinstance(embedding_str, str):
                        embedding_bytes = base64.b64decode(embedding_str)
                    elif isinstance(embedding_str, bytes):
                        embedding_bytes = embedding_str
                except Exception as e:
                    stats.warnings.append(f"Failed to decode embedding for chunk: {e}")

            # Check embedding model/dimensions compatibility
            if embedding_bytes:
                chunk_model = chunk_data.get("embedding_model")
                chunk_dims = chunk_data.get("embedding_dimensions")

                model_matches = (
                    not system_embedding_model
                    or not chunk_model
                    or system_embedding_model == chunk_model
                )
                dims_match = (
                    not system_embedding_dimensions
                    or not chunk_dims
                    or system_embedding_dimensions == chunk_dims
                )

                if not model_matches or not dims_match:
                    embedding_bytes = None  # Discard incompatible vectors
                    stats.embeddings_need_regeneration = True
                    stats.embedding_mismatch_reason = (
                        f"model mismatch: import has {chunk_model}, "
                        f"system uses {system_embedding_model}"
                    )

            try:
                chunk_record = {
                    "id": new_chunk_id,
                    "database_name": database_name,
                    "source_id": source_id,
                    "source_file_id": None,  # Not from source processing
                    "chunk_index": chunk_data.get("chunk_index", 0),
                    "content": chunk_data.get("content", ""),
                    # Phase 1 raw_content (Task 1.2, 2026-05-16): forward the
                    # pre-cleanup slice from the export bundle untouched.
                    # Pre-Phase-1 packages have no such key — .get() returns
                    # None and the column stays NULL (UI: "raw view
                    # unavailable for historical sources").
                    "raw_content": chunk_data.get("raw_content"),
                    "embedding": embedding_bytes,
                    "embedding_model": chunk_data.get("embedding_model"),
                    "embedding_dimensions": chunk_data.get("embedding_dimensions"),
                    "page_number": chunk_data.get("page_number"),
                    "section": chunk_data.get("section"),
                    "chunk_metadata": chunk_data.get("metadata", {}),
                    "status": chunk_data.get("status", "committed"),
                    "created_at": datetime.now(UTC),
                }

                self.sources_repository.create_chunk(chunk_record)

                if original_chunk_id:
                    mapper.map_chunk(original_chunk_id, new_chunk_id)
                stats.chunks_imported += 1

            except Exception as e:
                stats.warnings.append(f"Failed to import chunk: {e}")

        # Detect missing embeddings
        chunks_without_embeddings = sum(
            1 for chunk_data in chunks_data if not chunk_data.get("embedding")
        )
        if chunks_without_embeddings > 0:
            stats.embeddings_need_regeneration = True
            if not stats.embedding_mismatch_reason:
                stats.embedding_mismatch_reason = (
                    f"{chunks_without_embeddings} chunks imported without embedding vectors"
                )

    def _load_citations(
        self,
        citations_data: list[dict[str, Any]],
        source_id: str,
        mapper: IdMapper,
        stats: ImportStats,
        database_name: str,
    ) -> None:
        """Load citations for a source.

        Args:
            citations_data: List of citation dictionaries.
            source_id: Source ID for association.
            mapper: IdMapper for remapping chunk references.
            stats: ImportStats for recording statistics.
            database_name: Target database name.
        """
        for citation_data in citations_data:
            new_citation_id = generate_id("cit")

            # Remap chunk_id reference
            original_chunk_id = citation_data.get("chunk_id")
            new_chunk_id = mapper.get_chunk_id(original_chunk_id) if original_chunk_id else None

            try:
                citation_record = {
                    "id": new_citation_id,
                    "database_name": database_name,
                    "source_id": source_id,
                    "chunk_id": new_chunk_id,
                    "entity_uri": citation_data.get("entity_uri"),
                    "entity_label": citation_data.get("entity_label"),
                    "entity_type": citation_data.get("entity_type"),
                    "confidence": citation_data.get("confidence", 1.0),
                    "extraction_method": citation_data.get("extraction_method"),
                    "context_snippet": citation_data.get("context_snippet"),
                    "citation_metadata": citation_data.get("metadata", {}),
                    "created_at": datetime.now(UTC),
                }

                self.sources_repository.create_citation(citation_record)
                stats.citations_imported += 1

            except Exception as e:
                stats.warnings.append(f"Failed to import citation: {e}")

    def _apply_tags(
        self,
        tags_data: list[dict[str, Any]],
        source_id: str,
        stats: ImportStats,
    ) -> None:
        """Apply tags to a source.

        Args:
            tags_data: List of tag dictionaries.
            source_id: Source ID to tag.
            stats: ImportStats for recording statistics.
        """
        for tag_data in tags_data:
            try:
                tag_name = tag_data.get("tag_name")
                if not tag_name:
                    continue

                # Try to find or create tag
                if hasattr(self.sources_repository, "get_or_create_tag"):
                    tag_id = self.sources_repository.get_or_create_tag(
                        name=tag_name,
                        color=tag_data.get("tag_color"),
                        description=tag_data.get("tag_description"),
                    )
                    # Associate tag with source
                    if hasattr(self.sources_repository, "add_tag_to_source"):
                        self.sources_repository.add_tag_to_source(source_id, tag_id)

            except Exception as e:
                stats.warnings.append(f"Failed to apply tag '{tag_data.get('tag_name')}': {e}")


__all__ = ["SourceLoader"]
