# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Text and document preprocessing before entity extraction.

Handles normalization of raw entity data into a consistent structure
for downstream processing (template matching, embedding generation,
graph storage).
"""

from typing import Any

import structlog


logger = structlog.get_logger(__name__)


def normalize_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize entity structure for consistent downstream processing.

    Ensures every entity has all required fields with sensible defaults.
    Does NOT assign template IDs -- that happens at commit time under
    the per-source templates architecture.

    Args:
        entities: Raw entity dicts from extraction or deduplication.

    Returns:
        List of normalized entity dicts with consistent keys.

    """
    normalized_entities = []
    for idx, entity in enumerate(entities):
        normalized_entity = {
            "id": entity.get("id", f"entity_{idx}"),
            "name": entity.get("name", "Unknown"),
            "type": entity.get("type", "Unknown"),
            "description": entity.get("description", ""),
            "properties": entity.get("properties", {}),
            "aliases": entity.get("aliases", []),
            "confidence": entity.get("confidence", 1.0),
            "chunk_index": entity.get("chunk_index"),
            "sent_ref": entity.get("sent_ref"),
            "source_chunk_indices": entity.get("source_chunk_indices")
            or ([entity["chunk_index"]] if entity.get("chunk_index") is not None else None),
        }
        rejected = entity.get("rejected_aliases")
        if rejected:
            normalized_entity["rejected_aliases"] = rejected
        # Preserve the type-normalization audit trail when the upstream
        # ``normalize_entity_types`` (called from
        # ``finalize_distributed_extraction``) re-typed a generic entity
        # like ``Item`` -> ``Class``. The field is purely informational
        # but downstream UI / quality scoring rely on its presence.
        type_normalized_from = entity.get("type_normalized_from")
        if type_normalized_from:
            normalized_entity["type_normalized_from"] = type_normalized_from
        normalized_entities.append(normalized_entity)

    logger.info("entities_normalized", count=len(normalized_entities))
    return normalized_entities
