# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Entity Type Normalizer and Structural Entity Filter.

Normalizes entity types post-extraction based on description keywords.
Used to fix misclassified entities (e.g., "A class..." typed as "Item").

Also provides filtering for low-value structural entities like chapters,
sections, and parts that don't represent meaningful knowledge.

Example:
    from chaoscypher_core.services.sources.engine.extraction.utils import (
        normalize_entity_types,
        filter_structural_entities,
    )

    rules = {
        "Class": ["a class", "class that"],
        "Function": ["a function", "function that"],
    }

    entities = [
        {"name": "Mailbox", "type": "Item", "description": "A class in mailbox module"}
    ]

    normalized = normalize_entity_types(entities, rules)
    # Result: [{"name": "Mailbox", "type": "Class", ...}]
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog,
    )


logger = structlog.get_logger(__name__)


# Patterns for structural/navigational entities that add little knowledge value
# These are typically document organization markers, not meaningful entities
_STRUCTURAL_ENTITY_PATTERNS: list[re.Pattern[str]] = [
    # Chapters: "Chapter 1", "Chapter I", "Chapter XII", "CHAPTER ONE"
    re.compile(
        r"^chapter\s+(\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)$",
        re.IGNORECASE,
    ),
    # Sections: "Section 1", "Section A", "Section 1.2"
    re.compile(r"^section\s+[\d\w.]+$", re.IGNORECASE),
    # Parts: "Part 1", "Part I", "Part One"
    re.compile(
        r"^part\s+(\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)$", re.IGNORECASE
    ),
    # Books (as structural units): "Book 1", "Book I"
    re.compile(
        r"^book\s+(\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)$", re.IGNORECASE
    ),
    # Appendices: "Appendix A", "Appendix 1"
    re.compile(r"^appendix\s+[\d\w]+$", re.IGNORECASE),
    # Figures/Tables: "Figure 1", "Table 2.1"
    re.compile(r"^(figure|table|exhibit)\s+[\d.]+$", re.IGNORECASE),
    # Page numbers: "Page 1", "p. 42"
    re.compile(r"^(page|p\.?)\s*\d+$", re.IGNORECASE),
    # Volume: "Volume 1", "Vol. II"
    re.compile(r"^(volume|vol\.?)\s+(\d+|[ivxlcdm]+)$", re.IGNORECASE),
]

# Default structural entity types (used when domain config unavailable)
_DEFAULT_STRUCTURAL_ENTITY_TYPES: set[str] = {
    "STRUCTURAL_UNIT",
    "Structural Unit",
    "structural_unit",
    "DOCUMENT_SECTION",
    "Document Section",
    "CHAPTER",
    "Chapter",
    "SECTION",
    "Section",
}


def apply_type_aliases(
    entities: list[dict[str, Any]],
    aliases: Mapping[str, str],
    *,
    subtype_property: str = "entity_subtype",
) -> int:
    """Rewrite entity types via a domain alias map, preserving the original as a property.

    Some domain plugins declare two NodeTemplates that name effectively the
    same concept (e.g. literary's ``Historical Figure`` vs ``Character``).
    LLMs pick between them inconsistently, fragmenting the graph
    (Baron Funke can show up as ``Character`` in one run and
    ``Historical Figure`` in the next) and leaking refinement signal into
    the entity type when it belongs in a property.

    For each entity whose ``type`` exactly matches a key in ``aliases``,
    this function:

    1. Records the *original* type under
       ``entity["properties"][subtype_property]`` (creates ``properties``
       if absent, never overwrites an existing subtype value).
    2. Rewrites ``entity["type"]`` to the canonical value
       ``aliases[<original type>]``.

    Matching is case-sensitive — the plugin authors control both sides of
    the map. Entities are mutated in place. After one pass, an entity's
    type is no longer a key in the alias map, so the function is
    idempotent on repeated calls with the same map.

    Args:
        entities: Entity dicts (modified in-place).
        aliases: Mapping of alias type name → canonical type name. Empty
            map (or ``None``-equivalent) is a no-op.
        subtype_property: Property key under which to preserve the
            original type. Defaults to ``"entity_subtype"``.

    Returns:
        Count of entities whose type was rewritten.

    """
    if not entities or not aliases:
        return 0

    rewritten = 0
    for entity in entities:
        current_type = entity.get("type")
        if not isinstance(current_type, str):
            continue
        canonical = aliases.get(current_type)
        if canonical is None or canonical == current_type:
            continue

        # Preserve the alias under properties; first-write-wins so an
        # earlier alias pass isn't clobbered.
        if "properties" not in entity:
            entity["properties"] = {}
        properties = entity["properties"]
        if subtype_property not in properties:
            properties[subtype_property] = current_type

        entity["type"] = canonical
        rewritten += 1

    if rewritten:
        logger.info(
            "type_aliases_applied",
            count=rewritten,
            subtype_property=subtype_property,
        )

    return rewritten


def is_structural_entity(
    entity: dict[str, Any],
    structural_entity_types: set[str] | None = None,
) -> bool:
    """Check if an entity is a low-value structural/navigational element.

    Structural entities are document organization markers like chapters,
    sections, and parts that typically don't represent meaningful knowledge
    worth extracting into a knowledge graph.

    Args:
        entity: Entity dict with 'name' and optionally 'type' fields
        structural_entity_types: Domain-provided structural types (falls back to defaults)

    Returns:
        True if the entity appears to be a structural element

    """
    name = entity.get("name", "").strip()
    entity_type = entity.get("type", "")

    types_to_check = structural_entity_types or _DEFAULT_STRUCTURAL_ENTITY_TYPES

    # Check if type indicates structural
    if entity_type in types_to_check:
        return True

    # Check name against structural patterns
    return any(pattern.match(name) for pattern in _STRUCTURAL_ENTITY_PATTERNS)


def filter_structural_entities(
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]] | None = None,
    structural_entity_types: set[str] | None = None,
    filtering_log: FilteringLog | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, int | None]]:
    """Remove structural entities and update relationship indices.

    Filters out low-value structural entities (chapters, sections, etc.)
    that don't represent meaningful knowledge. Also remaps relationship
    indices to account for removed entities.

    Args:
        entities: List of extracted entities
        relationships: Optional list of relationships with source/target indices
        structural_entity_types: Domain-provided structural types (falls back to defaults)
        filtering_log: Optional log collector for pipeline diagnostics.

    Returns:
        Tuple of (filtered_entities, filtered_relationships, index_mapping)
        - filtered_entities: Entities with structural ones removed
        - filtered_relationships: Relationships with updated indices, invalid ones removed
        - index_mapping: Dict mapping old_index -> new_index (None if removed)

    Example:
        >>> entities = [
        ...     {"name": "Napoleon", "type": "PERSON"},
        ...     {"name": "Chapter 1", "type": "STRUCTURAL_UNIT"},
        ...     {"name": "France", "type": "LOCATION"},
        ... ]
        >>> rels = [{"source": 0, "target": 2, "type": "LOCATED_IN"}]
        >>> filtered_ents, filtered_rels, mapping = filter_structural_entities(entities, rels)
        >>> len(filtered_ents)
        2
        >>> filtered_rels[0]["source"], filtered_rels[0]["target"]
        (0, 1)

    """
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteredItem,
    )

    if relationships is None:
        relationships = []

    # Build index mapping: old_index -> new_index (or None if filtered)
    index_mapping: dict[int, int | None] = {}
    filtered_entities: list[dict[str, Any]] = []
    filtered_count = 0
    removed_items: list[FilteredItem] = []

    for old_idx, entity in enumerate(entities):
        if is_structural_entity(entity, structural_entity_types=structural_entity_types):
            index_mapping[old_idx] = None
            filtered_count += 1
            logger.debug(
                "structural_entity_filtered",
                name=entity.get("name"),
                type=entity.get("type"),
            )
            if filtering_log is not None:
                removed_items.append(
                    FilteredItem(
                        item_type="entity",
                        name=entity.get("name", "?"),
                        entity_type=entity.get("type", "?"),
                        reason="Structural entity (chapter/section/part)",
                    )
                )
        else:
            new_idx = len(filtered_entities)
            index_mapping[old_idx] = new_idx
            filtered_entities.append(entity)

    # Remap relationships, removing any that reference filtered entities
    filtered_relationships: list[dict[str, Any]] = []
    removed_rel_count = 0

    for rel in relationships:
        source_idx = rel.get("source")
        target_idx = rel.get("target")

        # Skip if indices are invalid
        if not isinstance(source_idx, int) or not isinstance(target_idx, int):
            removed_rel_count += 1
            continue

        new_source = index_mapping.get(source_idx)
        new_target = index_mapping.get(target_idx)

        # Skip if either entity was filtered out
        if new_source is None or new_target is None:
            removed_rel_count += 1
            continue

        # Create remapped relationship
        remapped_rel = rel.copy()
        remapped_rel["source"] = new_source
        remapped_rel["target"] = new_target
        filtered_relationships.append(remapped_rel)

    if filtered_count > 0:
        logger.info(
            "structural_entities_filtered",
            entities_removed=filtered_count,
            entities_remaining=len(filtered_entities),
            relationships_removed=removed_rel_count,
            relationships_remaining=len(filtered_relationships),
        )

    if filtering_log is not None:
        filtering_log.add_stage(
            "structural_entity_filter",
            input_count=len(entities),
            removed_count=filtered_count,
            items=removed_items,
        )

    return filtered_entities, filtered_relationships, index_mapping


# Default generic types that should be normalized when possible
_DEFAULT_GENERIC_TYPES: set[str] = {
    "Item",
    "UNKNOWN",
    "Unknown",
    "Thing",
    "Object",
    "Entity",
    "Concept",  # Often misused for code entities
}


def normalize_entity_types(
    entities: list[dict[str, Any]],
    rules: dict[str, list[str]],
    generic_types: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Normalize entity types based on description keywords.

    Only normalizes entities with generic types (Item, Unknown, etc.)
    when their description matches a normalization rule.

    Args:
        entities: List of extracted entities with name, type, description.
        rules: Mapping of target_type to list of trigger keywords.
               Example: {"Class": ["a class", "class that", ...]}
        generic_types: Domain-provided generic types (falls back to defaults)

    Returns:
        Entities with corrected types. Adds 'type_normalized_from' field
        to entities that were modified.

    Example:
        >>> rules = {"Class": ["a class", "class definition"]}
        >>> entities = [
        ...     {"name": "Foo", "type": "Item", "description": "A class..."}
        ... ]
        >>> result = normalize_entity_types(entities, rules)
        >>> result[0]["type"]
        'Class'
    """
    if not rules:
        return entities

    types_to_check = generic_types or _DEFAULT_GENERIC_TYPES
    normalized_count = 0

    for entity in entities:
        original_type = entity.get("type", "")
        description = entity.get("description", "").lower()

        # Only normalize generic/fallback types
        if original_type not in types_to_check:
            continue

        # Check each rule for a match
        for target_type, keywords in rules.items():
            if any(kw.lower() in description for kw in keywords):
                entity["type"] = target_type
                entity["type_normalized_from"] = original_type
                normalized_count += 1

                logger.debug(
                    "entity_type_normalized",
                    entity_name=entity.get("name"),
                    from_type=original_type,
                    to_type=target_type,
                    matched_description=description[:100],
                )
                break  # Only apply first matching rule

    if normalized_count > 0:
        logger.info(
            "type_normalization_complete",
            entities_normalized=normalized_count,
            total_entities=len(entities),
        )

    return entities


__all__ = [
    "filter_structural_entities",
    "is_structural_entity",
    "normalize_entity_types",
]
