# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Type Rescue System for extraction pipeline.

Salvages valid data from entities with invalid types instead of blindly
dropping them. Uses a three-tier approach:

1. **Junk Filter** — Drop obviously invalid entities (empty names, name == type).
2. **Property Absorption** — Convert property-like entities (e.g., "Personality Trait")
   into properties on their target entity using the domain's ``property_type_mapping``.
3. **Type Remapping** — Remap invalid types to valid domain types using normalization
   rules and keyword matching.

Entities that cannot be rescued are dropped (same as the current blind filter).
The key improvement is that Tier 3 rescues entities by remapping their type,
which preserves every relationship they had.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog,
    )


logger = structlog.get_logger(__name__)


def rescue_invalid_entity_types(  # noqa: C901, PLR0912, PLR0915
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    valid_types: set[str],
    normalization_rules: dict[str, list[str]],
    property_type_mapping: dict[str, dict[str, str]],
    filtering_log: FilteringLog | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[int, int | None],
    dict[str, int],
]:
    """Rescue entities with invalid types using a three-tier system.

    Instead of blindly dropping all entities with non-domain types, this
    function attempts to salvage valid data:

    - **Tier 1 (Junk):** Drop empty/whitespace names, single common words,
      and entities where name == type.
    - **Tier 2 (Property Absorption):** Convert property-like entities into
      properties on their target entity (via relationship or proximity).
    - **Tier 3 (Type Remapping):** Remap invalid types to valid domain types
      using description keywords and the type name itself as a keyword.

    Remaining unrescued entities are dropped (mapped to None).

    Args:
        entities: List of entity dicts from extraction.
        relationships: List of relationship dicts (source/target are int indices).
        valid_types: Set of allowed entity type names (case-sensitive).
        normalization_rules: Domain normalization rules mapping
            target_type → list of trigger keywords.
        property_type_mapping: Domain property-type mapping, mapping
            invalid_type → {"target_type": ..., "property": ...}.
        filtering_log: Optional log collector for pipeline diagnostics.

    Returns:
        Tuple of (rescued_entities, rescued_relationships, index_mapping,
        rescue_stats):
            - rescued_entities: Entities after rescue (valid + remapped).
            - rescued_relationships: Relationships remapped to new indices.
            - index_mapping: old_idx → new_idx (or None if removed).
            - rescue_stats: Counts of each rescue tier outcome.

    """
    if not valid_types or not entities:
        identity: dict[int, int | None] = {i: i for i in range(len(entities))}
        return entities, relationships, identity, {"total_invalid": 0}

    valid_lower = {t.lower() for t in valid_types}

    # Separate valid from invalid entities
    invalid_indices: list[int] = []
    for idx, entity in enumerate(entities):
        entity_type = (entity.get("type") or "").strip()
        if entity_type.lower() not in valid_lower:
            invalid_indices.append(idx)

    if not invalid_indices:
        identity_all: dict[int, int | None] = {i: i for i in range(len(entities))}
        return entities, relationships, identity_all, {"total_invalid": 0}

    # Build relationship lookup for Tier 2 (which entities are connected)
    rel_targets_for_entity: dict[int, list[int]] = {}
    rel_sources_for_entity: dict[int, list[int]] = {}
    for rel in relationships:
        src = rel.get("source")
        tgt = rel.get("target")
        if isinstance(src, int) and isinstance(tgt, int):
            rel_targets_for_entity.setdefault(src, []).append(tgt)
            rel_sources_for_entity.setdefault(tgt, []).append(src)

    # Process each invalid entity through the tiers
    # None = dropped, "absorbed" = absorbed into property, int = remapped index
    decisions: dict[int, str] = {}  # idx -> "junk" | "absorbed" | "remapped" | "dropped"
    remap_new_types: dict[int, str] = {}  # idx -> new_type for tier 3

    stats = {
        "total_invalid": len(invalid_indices),
        "tier1_junk": 0,
        "tier2_absorbed": 0,
        "tier3_remapped": 0,
        "unrescued_dropped": 0,
    }

    for idx in invalid_indices:
        entity = entities[idx]
        entity_type = (entity.get("type") or "").strip()
        entity_name = (entity.get("name") or "").strip()

        # Tier 1: Junk Filter
        if _is_junk_entity(entity_name, entity_type):
            decisions[idx] = "junk"
            stats["tier1_junk"] += 1
            continue

        # Tier 2: Property Absorption
        if property_type_mapping and _try_absorb_as_property(
            idx,
            entity,
            entities,
            entity_type,
            property_type_mapping,
            rel_targets_for_entity,
            rel_sources_for_entity,
        ):
            decisions[idx] = "absorbed"
            stats["tier2_absorbed"] += 1
            continue

        # Tier 3: Type Remapping
        new_type = _try_remap_type(entity, entity_type, normalization_rules, valid_types)
        if new_type:
            decisions[idx] = "remapped"
            remap_new_types[idx] = new_type
            stats["tier3_remapped"] += 1
            continue

        # Unrescued — drop
        decisions[idx] = "dropped"
        stats["unrescued_dropped"] += 1

    # Build the rescued entity list and index mapping
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteredItem,
    )

    rescued: list[dict[str, Any]] = []
    index_mapping: dict[int, int | None] = {}
    removed_items: list[FilteredItem] = []

    for old_idx, entity in enumerate(entities):
        if old_idx not in decisions:
            # Valid entity — keep as-is
            new_idx = len(rescued)
            rescued.append(entity)
            index_mapping[old_idx] = new_idx
        elif decisions[old_idx] == "remapped":
            # Tier 3 — remap type and keep
            remapped_entity = entity.copy()
            remapped_entity["type"] = remap_new_types[old_idx]
            remapped_entity["type_rescued_from"] = (entity.get("type") or "").strip()
            new_idx = len(rescued)
            rescued.append(remapped_entity)
            index_mapping[old_idx] = new_idx
        else:
            # Tier 1 (junk), Tier 2 (absorbed), or unrescued — drop
            index_mapping[old_idx] = None
            if filtering_log is not None:
                tier = decisions[old_idx]
                e_name = (entity.get("name") or "").strip()
                e_type = (entity.get("type") or "").strip()
                tier_label = {
                    "junk": "Junk entity (tier 1)",
                    "absorbed": "Absorbed as property (tier 2)",
                    "dropped": "Unrescuable invalid type (dropped)",
                }
                removed_items.append(
                    FilteredItem(
                        item_type="entity",
                        name=e_name or "(empty)",
                        entity_type=e_type,
                        reason=tier_label.get(tier, tier),
                        details={"tier": tier},
                    )
                )

    # Remap relationships using the index mapping
    from chaoscypher_core.services.sources.engine.deduplication import EntityProcessor

    rescued_relationships = EntityProcessor.remap_relationship_indices(relationships, index_mapping)

    # Log summary
    if stats["total_invalid"] > 0:
        logger.info(
            "type_rescue_complete",
            **stats,
            kept=len(rescued),
            original=len(entities),
        )

    if filtering_log is not None:
        total_dropped = stats["tier1_junk"] + stats["tier2_absorbed"] + stats["unrescued_dropped"]
        filtering_log.add_stage(
            "type_rescue",
            input_count=len(entities),
            removed_count=total_dropped,
            items=removed_items,
        )

    return rescued, rescued_relationships, index_mapping, stats


def _is_junk_entity(name: str, entity_type: str) -> bool:
    """Check if an entity is obvious junk that should be dropped.

    Junk entities include:
    - Empty or whitespace-only names
    - Single common/stop words
    - Entities where name equals type (e.g., name="Event", type="Event")

    Args:
        name: Entity name (already stripped).
        entity_type: Entity type (already stripped).

    Returns:
        True if entity is junk and should be dropped.

    """
    if not name:
        return True

    # Name equals type (case-insensitive)
    if name.lower() == entity_type.lower():
        return True

    # Single common word (too generic to be useful)
    _common_single_words = frozenset(
        {
            "the",
            "a",
            "an",
            "it",
            "is",
            "was",
            "were",
            "be",
            "been",
            "and",
            "or",
            "but",
            "not",
            "no",
            "yes",
            "he",
            "she",
            "they",
            "him",
            "her",
            "his",
            "this",
            "that",
            "these",
            "those",
            "who",
            "what",
            "where",
            "when",
            "how",
            "why",
        }
    )
    return name.lower() in _common_single_words


def _try_absorb_as_property(
    entity_idx: int,
    entity: dict[str, Any],
    all_entities: list[dict[str, Any]],
    entity_type: str,
    property_type_mapping: dict[str, dict[str, str]],
    rel_targets: dict[int, list[int]],
    rel_sources: dict[int, list[int]],
) -> bool:
    """Try to absorb an entity as a property on a target entity.

    Looks up the entity type in ``property_type_mapping``, finds a suitable
    target entity (via relationship or proximity), and applies the entity's
    name as a property value on the target.

    Args:
        entity_idx: Index of the entity to absorb.
        entity: The entity dict.
        all_entities: Full entity list for target lookup.
        entity_type: The entity's type string.
        property_type_mapping: Maps invalid types to target_type + property name.
        rel_targets: Mapping of entity_idx → list of target indices via relationships.
        rel_sources: Mapping of entity_idx → list of source indices via relationships.

    Returns:
        True if the entity was absorbed as a property on a target.

    """
    mapping = property_type_mapping.get(entity_type)
    if not mapping:
        return False

    target_type = mapping.get("target_type", "")
    property_name = mapping.get("property", "")
    if not target_type or not property_name:
        return False

    target_type_lower = target_type.lower()
    entity_name = (entity.get("name") or "").strip()
    if not entity_name:
        return False

    # Strategy (a): Find target via relationship
    connected_indices = set(rel_targets.get(entity_idx, []) + rel_sources.get(entity_idx, []))
    for connected_idx in connected_indices:
        if connected_idx < 0 or connected_idx >= len(all_entities):
            continue
        connected = all_entities[connected_idx]
        connected_type = (connected.get("type") or "").strip()
        if connected_type.lower() == target_type_lower:
            _apply_property(connected, property_name, entity_name)
            logger.debug(
                "type_rescue_property_absorbed",
                entity_name=entity_name,
                entity_type=entity_type,
                target_name=connected.get("name"),
                property_name=property_name,
                method="relationship",
            )
            return True

    # Strategy (b): Find target via proximity (same chunk, within 3 indices)
    entity_chunk = entity.get("chunk_index")
    for offset in range(1, 4):
        for candidate_idx in [entity_idx - offset, entity_idx + offset]:
            if candidate_idx < 0 or candidate_idx >= len(all_entities):
                continue
            candidate = all_entities[candidate_idx]
            candidate_type = (candidate.get("type") or "").strip()
            candidate_chunk = candidate.get("chunk_index")
            if candidate_type.lower() == target_type_lower and candidate_chunk == entity_chunk:
                _apply_property(candidate, property_name, entity_name)
                logger.debug(
                    "type_rescue_property_absorbed",
                    entity_name=entity_name,
                    entity_type=entity_type,
                    target_name=candidate.get("name"),
                    property_name=property_name,
                    method="proximity",
                )
                return True

    return False


def _apply_property(entity: dict[str, Any], property_name: str, value: str) -> None:
    """Apply a property value to an entity, appending with semicolon if exists.

    Args:
        entity: Target entity dict (modified in-place).
        property_name: Property key to set.
        value: Property value to set.

    """
    if "properties" not in entity:
        entity["properties"] = {}
    existing = entity["properties"].get(property_name)
    if existing:
        entity["properties"][property_name] = f"{existing}; {value}"
    else:
        entity["properties"][property_name] = value


def _try_remap_type(
    entity: dict[str, Any],
    entity_type: str,
    normalization_rules: dict[str, list[str]],
    valid_types: set[str],
) -> str | None:
    """Try to remap an invalid entity type to a valid domain type.

    Two strategies:
    1. Check entity description against normalization rule keywords.
    2. Check if the invalid type name itself matches a normalization keyword.

    Args:
        entity: The entity dict.
        entity_type: The entity's invalid type string.
        normalization_rules: Domain rules mapping target_type → keywords.
        valid_types: Set of valid type names.

    Returns:
        The new valid type name, or None if no match found.

    """
    description = (entity.get("description") or "").lower()
    type_lower = entity_type.lower()

    for target_type, keywords in normalization_rules.items():
        if target_type not in valid_types:
            continue

        # Strategy 1: Description keywords match
        if description and any(kw.lower() in description for kw in keywords):
            return target_type

        # Strategy 2: The invalid type name itself is a keyword
        if any(type_lower == kw.lower() for kw in keywords):
            return target_type
        # Also check if type_lower is a substring of any keyword or vice versa
        if any(type_lower in kw.lower() or kw.lower() in type_lower for kw in keywords):
            return target_type

    return None


__all__: list[str] = [
    "rescue_invalid_entity_types",
]
