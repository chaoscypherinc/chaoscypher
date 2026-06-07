# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Metadata handling and formatting for CCX export.

Provides pure functions for building export-ready dictionaries from source,
chunk, and citation records, as well as serialization helpers for JSON/JSONL
output with checksum calculation and statistics aggregation.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings

from chaoscypher_core.services.export.engine.stats import (
    calculate_knowledge_stats,
    calculate_lens_stats,
    calculate_source_stats,
    calculate_template_stats,
    calculate_workflow_stats,
)
from chaoscypher_core.services.export.utils import FileIntegrityChecker


logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Bytes serializer (used as ``default`` argument for ``json.dumps``)
# ---------------------------------------------------------------------------


def serialize_bytes(obj: Any) -> Any:
    """Custom JSON serializer for bytes to base64 strings.

    Used to properly encode embedding bytes in JSONL export format.
    Without this, bytes would be serialized as "b'...'" which cannot
    be decoded on import.

    Args:
        obj: Object to serialize.

    Returns:
        Base64 string for bytes, str(obj) for other types.

    """
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode("utf-8")
    return str(obj)


# ---------------------------------------------------------------------------
# Dict builders for sources, chunks, citations
# ---------------------------------------------------------------------------


def build_source_dict(source: dict[str, Any]) -> dict[str, Any]:
    """Build source dictionary from source dict.

    Args:
        source: Source dictionary from repository.

    Returns:
        Source dictionary formatted for export.

    """
    created_at = source.get("created_at")
    updated_at = source.get("updated_at")

    return {
        "id": source["id"],
        "database_name": source.get("database_name"),
        "version": source.get("version"),
        "parent_id": source.get("parent_id"),
        "source_type": source.get("source_type"),
        "title": source.get("title"),
        "origin_url": source.get("origin_url"),
        "chunk_count": source.get("chunk_count"),
        "total_content_length": source.get("total_content_length"),
        "embedding_model": source.get("embedding_model"),
        "embedding_dimensions": source.get("embedding_dimensions"),
        "status": source.get("status"),
        "created_at": created_at.isoformat()
        if created_at and hasattr(created_at, "isoformat")
        else created_at,
        "updated_at": updated_at.isoformat()
        if updated_at and hasattr(updated_at, "isoformat")
        else updated_at,
        "metadata": source.get("user_metadata"),
    }


def build_chunk_dict(chunk: dict[str, Any], include_embeddings: bool = False) -> dict[str, Any]:
    """Build chunk dictionary from chunk dict.

    Args:
        chunk: Chunk dictionary from repository.
        include_embeddings: Whether to include embedding vectors. Metadata
            (embedding_model, embedding_dimensions) is always preserved.

    Returns:
        Chunk dictionary formatted for export.

    """
    created_at = chunk.get("created_at")
    result: dict[str, Any] = {
        "id": chunk["id"],
        "chunk_index": chunk.get("chunk_index"),
        "content": chunk.get("content"),
        "page_number": chunk.get("page_number"),
        "section": chunk.get("section"),
        "status": chunk.get("status"),
        "embedding_model": chunk.get("embedding_model"),
        "embedding_dimensions": chunk.get("embedding_dimensions"),
        "metadata": chunk.get("chunk_metadata"),
        "created_at": created_at.isoformat()
        if created_at and hasattr(created_at, "isoformat")
        else created_at,
    }
    if include_embeddings:
        result["embedding"] = chunk.get("embedding")
    return result


def build_citation_dict(citation: dict[str, Any]) -> dict[str, Any]:
    """Build citation dictionary from citation dict.

    Args:
        citation: Citation dictionary from repository.

    Returns:
        Citation dictionary formatted for export.

    """
    created_at = citation.get("created_at")
    return {
        "id": citation["id"],
        "entity_uri": citation.get("entity_uri"),
        "entity_label": citation.get("entity_label"),
        "entity_type": citation.get("entity_type"),
        "chunk_id": citation.get("chunk_id"),
        "confidence": citation.get("confidence"),
        "extraction_method": citation.get("extraction_method"),
        "context_snippet": citation.get("context_snippet"),
        "created_at": created_at.isoformat()
        if created_at and hasattr(created_at, "isoformat")
        else created_at,
        "metadata": citation.get("citation_metadata"),
    }


# ---------------------------------------------------------------------------
# Serialization and checksum
# ---------------------------------------------------------------------------


def is_file_empty(file_type: str, data: Any) -> bool:
    """Check if a file type has any actual content.

    Args:
        file_type: Type of file (templates, knowledge, etc.).
        data: Data for the file.

    Returns:
        True if empty, False if has content.

    """
    if file_type == "templates":
        return len(data.get("templates", [])) == 0
    if file_type == "sources":
        return len(data) == 0 if isinstance(data, list) else True
    if file_type in ["knowledge", "lenses", "workflows"]:
        return (
            len(data.get("nodes", [])) == 0
            and len(data.get("edges", [])) == 0
            and len(data.get("triggers", [])) == 0
        )
    return True


def serialize_and_checksum(
    separated_data: dict[str, Any],
    include_embeddings: bool = False,
) -> dict[str, dict[str, Any]]:
    """Serialize data to JSON/JSONL and calculate checksums (only for non-empty content).

    Args:
        separated_data: Separated node/edge data.
        include_embeddings: Whether to include embedding vectors in output.

    Returns:
        Dict mapping file type to {json, bytes, sha512, sha256} (only non-empty files).

    """
    file_data: dict[str, dict[str, Any]] = {}

    # Strip knowledge node embeddings if not included
    knowledge_nodes = separated_data["knowledge_nodes"]
    if not include_embeddings:
        knowledge_nodes = [
            {k: v for k, v in node.items() if k != "embedding"} for node in knowledge_nodes
        ]

    # Process each file type
    files_to_process = [
        ("templates", {"templates": separated_data["templates"]}),
        (
            "knowledge",
            {
                "nodes": knowledge_nodes,
                "edges": separated_data["knowledge_edges"],
            },
        ),
        (
            "lenses",
            {"nodes": separated_data["lens_nodes"], "edges": separated_data["lens_edges"]},
        ),
        (
            "workflows",
            {
                "nodes": separated_data["workflow_nodes"],
                "edges": separated_data["workflow_edges"],
                "triggers": separated_data["triggers"],
            },
        ),
        ("sources", separated_data["sources"]),  # Pass sources list directly for JSONL
    ]

    for file_type, data in files_to_process:
        # Check if this file has any actual content
        empty = is_file_empty(file_type, data)

        # Skip empty files
        if empty:
            extension = "jsonl" if file_type == "sources" else "jsonld"
            logger.debug("skipping_empty_file", file_type=file_type, extension=extension)
            continue

        # Serialize based on format
        if file_type == "sources":
            # JSONL format: each source on its own line
            # Use serialize_bytes to properly encode embeddings as base64
            json_str = "\n".join(json.dumps(source, default=serialize_bytes) for source in data)
        else:
            # Regular JSON-LD format with indentation
            json_str = json.dumps(data, indent=2, default=serialize_bytes)

        json_bytes = json_str.encode("utf-8")

        # Calculate checksums
        sha512, sha256 = FileIntegrityChecker.calculate_checksums(json_bytes)

        file_data[file_type] = {
            "json": json_str,
            "bytes": json_bytes,
            "sha512": sha512,
            "sha256": sha256,
            "size": len(json_bytes),
        }

    return file_data


# ---------------------------------------------------------------------------
# Statistics calculation
# ---------------------------------------------------------------------------


def calculate_all_stats(
    *,
    separated_data: dict[str, Any],
    settings: EngineSettings,
    include_templates: bool,
    include_knowledge: bool,
    include_lenses: bool,
    include_workflows: bool,
    include_sources: bool,
    include_embeddings: bool = False,
) -> dict[str, Any | None]:
    """Calculate statistics for all content types.

    Args:
        separated_data: Separated node/edge data.
        settings: Export settings.
        include_templates: Whether templates are included.
        include_knowledge: Whether knowledge is included.
        include_lenses: Whether lenses are included.
        include_workflows: Whether workflows are included.
        include_sources: Whether sources are included.
        include_embeddings: Whether embedding vectors are included in export.

    Returns:
        Dict with template_stats, knowledge_stats, lens_stats, workflow_stats, source_stats.

    """
    stats: dict[str, Any | None] = {}

    # Calculate template stats (using extracted calculator)
    template_stats: Any | None
    if include_templates:
        template_stats = calculate_template_stats(separated_data["templates"])
    else:
        template_stats = None
    stats["template_stats"] = template_stats

    # Calculate knowledge stats (using extracted calculator)
    knowledge_stats: Any | None
    if include_knowledge:
        knowledge_stats = calculate_knowledge_stats(
            nodes=separated_data["knowledge_nodes"],
            edges=separated_data["knowledge_edges"],
            settings=settings,
            include_embeddings=include_embeddings,
        )
    else:
        knowledge_stats = None
    stats["knowledge_stats"] = knowledge_stats

    # Calculate lens stats (using extracted calculator)
    lens_stats: Any | None = (
        calculate_lens_stats(separated_data["lens_nodes"]) if include_lenses else None
    )
    stats["lens_stats"] = lens_stats

    # Calculate workflow stats (using extracted calculator)
    workflow_stats: Any | None
    if include_workflows:
        workflow_stats = calculate_workflow_stats(
            nodes=separated_data["workflow_nodes"], triggers=separated_data["triggers"]
        )
    else:
        workflow_stats = None
    stats["workflow_stats"] = workflow_stats

    # Calculate source stats (using extracted calculator)
    source_stats: Any | None
    if include_sources:
        source_stats = calculate_source_stats(
            sources=separated_data["sources"],
            include_embeddings=include_embeddings,
        )
    else:
        source_stats = None
    stats["source_stats"] = source_stats

    return stats
