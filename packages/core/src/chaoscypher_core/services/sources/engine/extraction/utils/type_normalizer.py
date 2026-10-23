# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Entity Type Normalizer and Structural Entity Filter.

Normalizes generic entity types post-extraction based on description keywords
(e.g. "A city on the river" typed as "Thing" becomes "Location"); a core type
is never generic. Rewrites pack aliases and old template names to their core
type and subtype (:func:`canonicalize_entity_types`).

Also provides filtering for low-value structural entities like chapters,
sections, and parts that don't represent meaningful knowledge.

Example:
    from chaoscypher_core.services.sources.engine.extraction.utils import (
        normalize_entity_types,
        filter_structural_entities,
    )

    rules = {"Location": ["city", "place"], "Work": ["book", "report"]}

    entities = [
        {"name": "Mailbox", "type": "Thing", "description": "A report on the mailbox module"}
    ]

    normalized = normalize_entity_types(entities, rules)
    # Result: [{"name": "Mailbox", "type": "Work", ...}]
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.sources.engine.extraction.domains.core_types import CORE_TYPES
from chaoscypher_core.services.sources.engine.extraction.utils.constants import (
    UNKNOWN_ENTITY_TYPE,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
    SUBTYPE_PROPERTY,
    TypeVocabulary,
    flag_unplaced_type,
    has_phrase,
)


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


def canonicalize_entity_types(
    entities: list[dict[str, Any]],
    vocabulary: TypeVocabulary,
    *,
    subtype_property: str = SUBTYPE_PROPERTY,
) -> int:
    """Rewrite every entity type a vocabulary resolves to its core type, the subtype as a property.

    The finalize paths' one type rewrite (``post_extraction.apply_domain_type_aliases``,
    ``extractor.extract_entities_from_groups``): :meth:`TypeVocabulary.place`
    (a listed type in any case, ``Core/subtype``, a pack alias, exact key
    first, a mapping-table row, a pack subtype) gives ``(core, subtype)``; the
    entity takes the core type and keeps the subtype under
    ``properties[subtype_property]`` unless it already carries one. A
    ``Core/subtype`` whose subtype no pack offers is kept on its core type
    flagged ``unplaced_type`` (``type_rescue.flag_unplaced_type``): what the
    MCP path's entities, which no per-chunk rescue read, are placed by. A
    type nothing places is left as it is (the per-chunk rescue already typed
    every entity a plan extracted; this is for records that predate it).
    Entities are mutated in place; a second pass rewrites nothing.

    Returns:
        Count of entities whose type or subtype was rewritten.
    """
    if not entities:
        return 0
    rewritten = 0
    for entity in entities:
        current_type = entity.get("type")
        if not isinstance(current_type, str) or vocabulary.is_listed(current_type):
            continue
        found = vocabulary.place(current_type)
        if found is None or found.core is None:
            continue
        if found.unplaced:
            entity.update(flag_unplaced_type(entity, core=found.core))
            rewritten += 1
            continue
        core, subtype = found.core, found.subtype
        if subtype:
            properties = entity.setdefault("properties", {})
            if isinstance(properties, dict) and not properties.get(subtype_property):
                properties[subtype_property] = subtype
        entity["type"] = core
        rewritten += 1

    if rewritten:
        logger.info("type_aliases_applied", count=rewritten, subtype_property=subtype_property)
    return rewritten


def apply_type_aliases(
    entities: list[dict[str, Any]],
    aliases: Mapping[str, str] | None,
    *,
    subtype_property: str = SUBTYPE_PROPERTY,
    packs: Sequence[str] = (),
) -> int:
    """Rewrite entity types to core types via a pack's alias map and the mapping table.

    A pack may alias a name it does not declare to one of its subtypes
    (literary's ``Historical Figure`` -> ``Character``), or to a core type
    (investigation's ``Suspect`` -> ``Person``). For each entity whose type is
    not a core type, the alias (exact key, as the plugin writes it) is read
    first, then ``core_types.core_type_of`` (``packs``' qualified rows first):
    ``Historical Figure`` becomes ``Person`` with ``entity_subtype``
    ``character``; an alias to a core type keeps the table's subtype of the
    written name when that is a kind of it (``Historical Event`` ->
    ``Event``/``historical_event``). A subtype the record already carries is
    never overwritten (:func:`canonicalize_entity_types`).

    Args:
        entities: Entity dicts (modified in-place).
        aliases: Alias type name -> canonical type or subtype name.
        subtype_property: Property key the subtype is kept under.
        packs: The source's pack names (the table's pack-qualified rows).

    Returns:
        Count of entities whose type was rewritten.
    """
    vocabulary = TypeVocabulary(packs=tuple(packs), aliases=dict(aliases or {}))
    return canonicalize_entity_types(entities, vocabulary, subtype_property=subtype_property)


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


# Default generic types that should be normalized when possible. No core type is
# one (Object and Concept are core types since spec 2026-10-07): the normalizer
# never retypes a core type, whatever a template's ``is_generic`` flag says.
_DEFAULT_GENERIC_TYPES: set[str] = {UNKNOWN_ENTITY_TYPE, "Unknown", "Thing", "Entity"}


def normalize_entity_types(
    entities: list[dict[str, Any]],
    rules: dict[str, list[str]],
    generic_types: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Normalize entity types based on description keywords.

    Only normalizes entities with generic types (Thing, Unknown, etc.;
    never a core type) when their description writes a rule's keyword as
    whole words (``type_rescue.has_phrase``).

    Args:
        entities: List of extracted entities with name, type, description.
        rules: Mapping of target_type to list of trigger keywords.
               Example: {"Class": ["a class", "class that", ...]}
        generic_types: Domain-provided generic types (falls back to defaults)

    Returns:
        Entities with corrected types. Adds 'type_normalized_from' field
        to entities that were modified.

    Example:
        >>> rules = {"Location": ["city", "place"]}
        >>> entities = [
        ...     {"name": "Foo", "type": "Thing", "description": "A city..."}
        ... ]
        >>> result = normalize_entity_types(entities, rules)
        >>> result[0]["type"]
        'Location'
    """
    if not rules:
        return entities

    types_to_check = (generic_types or _DEFAULT_GENERIC_TYPES) - set(CORE_TYPES)
    normalized_count = 0

    for entity in entities:
        original_type = entity.get("type", "")
        description = entity.get("description", "").lower()

        # Only normalize generic/fallback types
        if original_type not in types_to_check:
            continue

        # Check each rule for a match
        for target_type, keywords in rules.items():
            if any(has_phrase(description, kw) for kw in keywords):
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
    "apply_type_aliases",
    "canonicalize_entity_types",
    "filter_structural_entities",
    "is_structural_entity",
    "normalize_entity_types",
]
