# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Entity cleanup and validation utilities.

Standalone functions for cleaning, validating, and transforming entity
and relationship data extracted by the AI pipeline.

Functions:
- apply_properties_to_entities: Attach parsed P| properties to their entities
- validate_relationships: Check bounds, reject self-loops, optionally resolve names
- parse_index: Parse a source/target value as an integer index
- filter_excluded_entities: Remove entities matching domain exclusion rules
- deduplicate_relationships: Chunk-aware dedup of (source, target, type) relationships
- clean_descriptor_aliases: Remove aliases that are descriptors, not name variants
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        ExclusionRule,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteredItem,
        FilteringLog,
    )


logger = structlog.get_logger(__name__)


def _log_filtered(
    filtering_log: FilteringLog | None,
    target: list[FilteredItem],
    *,
    item_type: str,
    name: str,
    entity_type: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Append a FilteredItem to *target* if filtering logging is enabled.

    Centralises the repeated guard-and-append pattern used throughout the
    extraction pipeline.

    Args:
        filtering_log: The active log, or ``None`` if logging is disabled.
        target: The local list that collects removed-item records.
        item_type: ``"entity"`` or ``"relationship"``.
        name: Display name for the filtered item.
        entity_type: Entity or relationship type label.
        reason: Human-readable removal reason.
        details: Optional extra context dict.

    """
    if filtering_log is None:
        return

    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteredItem,
    )

    target.append(
        FilteredItem(
            item_type=item_type,
            name=name,
            entity_type=entity_type,
            reason=reason,
            details=details,
        )
    )


def _build_name_index(entities: list[dict[str, Any]]) -> dict[str, int]:
    """Build a case-insensitive name/alias → entity index mapping.

    Args:
        entities: List of entity dicts.

    Returns:
        Mapping from lowercased name/alias to entity index.

    """
    name_to_idx: dict[str, int] = {}
    for i, entity in enumerate(entities):
        name = entity.get("name", "")
        if name:
            name_to_idx[name.lower()] = i
            for alias in entity.get("aliases", []):
                if alias:
                    name_to_idx[alias.lower()] = i
    return name_to_idx


def _resolve_property_entity(
    prop: dict[str, Any],
    entity_count: int,
    name_to_idx: dict[str, int] | None,
) -> tuple[int | None, dict[str, int] | None]:
    """Resolve which entity a property belongs to.

    Resolution order: proximity → V2 index → V1 name.

    Args:
        prop: Property dict (may have _proximity_entity_index, entity_index, or entity_name).
        entity_count: Number of entities for bounds checking.
        name_to_idx: Lazy name mapping (None if not yet built; returned if built here).

    Returns:
        Tuple of (entity_index_or_None, name_to_idx).

    """
    # Priority 1: Proximity-based (most reliable)
    proximity_idx = prop.get("_proximity_entity_index")
    if isinstance(proximity_idx, int) and 0 <= proximity_idx < entity_count:
        return proximity_idx, name_to_idx

    # Priority 2: V2 explicit index
    if "entity_index" in prop:
        idx = prop["entity_index"]
        if isinstance(idx, int) and 0 <= idx < entity_count:
            return idx, name_to_idx

    # Priority 3: V1 name-based fallback
    entity_name = prop.get("entity_name", "")
    if not entity_name:
        return None, name_to_idx
    # name_to_idx is built on first use by the caller; signalled by returning None
    return (
        name_to_idx.get(entity_name.lower()) if name_to_idx is not None else None,
        name_to_idx,
    )


def apply_properties_to_entities(
    entities: list[dict[str, Any]],
    properties: list[dict[str, Any]],
) -> None:
    """Apply parsed properties to their corresponding entities.

    Resolution order (first match wins):
    1. **Proximity** (``_proximity_entity_index``): the entity that the P|
       line physically followed in the LLM output.  This is the most reliable
       signal because LLMs typically emit P| lines right after their parent E|.
    2. **Index-based** (V2): ``entity_index`` — the explicit 0-based index the
       LLM wrote.  Used when there is no proximity annotation.
    3. **Name-based** (V1): ``entity_name`` matched against entity names and
       aliases (case-insensitive).

    A **first-write-wins** guard prevents a second batch of P| lines
    (commonly emitted by LLMs at the end of output with wrong indices)
    from overwriting correct first-batch assignments.

    Args:
        entities: List of entity dicts (modified in-place).
        properties: List of property dicts with entity_name or entity_index, key, value.

    """
    if not properties:
        return

    entity_count = len(entities)
    name_to_idx: dict[str, int] | None = None
    assigned: set[tuple[int, str]] = set()

    for prop in properties:
        key = prop.get("key", "")
        if not key:
            continue

        # Lazy-build name index on first V1 property
        if name_to_idx is None and "entity_name" in prop:
            name_to_idx = _build_name_index(entities)

        entity_idx, name_to_idx = _resolve_property_entity(prop, entity_count, name_to_idx)
        if entity_idx is None:
            continue

        # First-write-wins: skip if this (entity, key) was already assigned
        assignment_key = (entity_idx, key)
        if assignment_key in assigned:
            continue
        assigned.add(assignment_key)

        if "properties" not in entities[entity_idx]:
            entities[entity_idx]["properties"] = {}

        entities[entity_idx]["properties"][key] = prop.get("value", "")


def parse_index(value: Any) -> int | None:
    """Parse a source/target value as an integer index.

    Args:
        value: Integer or string value.

    Returns:
        Integer index or None if unparseable.

    """
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def validate_relationships(
    raw_relationships: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    resolve_names: bool = False,
    filtering_log: FilteringLog | None = None,
    *,
    allow_self_loops: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Validate relationship indices, optionally resolving to entity names.

    Checks bounds, rejects self-loops, and normalizes source/target.
    When *resolve_names* is True, converts validated indices to entity
    names for downstream compatibility.

    Args:
        raw_relationships: Relationships with integer source/target indices.
        entities: Entity list for bounds checking.
        resolve_names: If True, replace indices with entity names.
        filtering_log: Optional log collector for pipeline diagnostics.
        allow_self_loops: When True, keep relationships whose source and target
            resolve to the same entity index instead of dropping them.
            Defaults to False (preserves the historical behaviour).

    Returns:
        Tuple of (valid_relationships, invalid_count).

    """
    max_index = len(entities) - 1
    valid_relationships: list[dict[str, Any]] = []
    invalid_count = 0
    removed_items: list[FilteredItem] = []

    for rel in raw_relationships:
        source = rel.get("source")
        target = rel.get("target")

        source_idx = parse_index(source)
        target_idx = parse_index(target)

        if source_idx is None or target_idx is None:
            invalid_count += 1
            _log_filtered(
                filtering_log,
                removed_items,
                item_type="relationship",
                name=f"{source} -> {target}",
                entity_type=rel.get("type", "?"),
                reason="Non-integer source/target index",
            )
            continue

        if source_idx < 0 or source_idx > max_index:
            invalid_count += 1
            # Target may be resolvable even if source is out of bounds
            tgt_label = (
                entities[target_idx].get("name", f"Entity {target_idx}")
                if 0 <= target_idx <= max_index
                else str(target_idx)
            )
            _log_filtered(
                filtering_log,
                removed_items,
                item_type="relationship",
                name=f"[#{source_idx}] -> {tgt_label}",
                entity_type=rel.get("type", "?"),
                reason=f"Source index {source_idx} out of bounds (max={max_index})",
            )
            continue

        if target_idx < 0 or target_idx > max_index:
            invalid_count += 1
            src_label = entities[source_idx].get("name", f"Entity {source_idx}")
            _log_filtered(
                filtering_log,
                removed_items,
                item_type="relationship",
                name=f"{src_label} -> [#{target_idx}]",
                entity_type=rel.get("type", "?"),
                reason=f"Target index {target_idx} out of bounds (max={max_index})",
            )
            continue

        if source_idx == target_idx and not allow_self_loops:
            invalid_count += 1
            src_name = entities[source_idx].get("name", f"Entity {source_idx}")
            _log_filtered(
                filtering_log,
                removed_items,
                item_type="relationship",
                name=f"{src_name} -> {src_name}",
                entity_type=rel.get("type", "?"),
                reason="Self-loop (source == target)",
            )
            continue

        if resolve_names:
            rel["source"] = entities[source_idx].get("name", f"Entity {source_idx}")
            rel["target"] = entities[target_idx].get("name", f"Entity {target_idx}")
        else:
            rel["source"] = source_idx
            rel["target"] = target_idx
        valid_relationships.append(rel)

    if filtering_log is not None:
        filtering_log.add_stage(
            "relationship_index_validation",
            input_count=len(raw_relationships),
            removed_count=invalid_count,
            items=removed_items,
        )

    return valid_relationships, invalid_count


def _resolve_rel_name(
    rel: dict[str, Any],
    entities: list[dict[str, Any]] | None,
) -> str:
    """Resolve a relationship's source/target to human-readable entity names.

    Args:
        rel: Relationship dict with ``source`` and ``target`` (int indices).
        entities: Entity list for name lookup (may be None).

    Returns:
        String like ``"Harry Potter -> Hogwarts"`` or ``"0 -> 1"`` as fallback.

    """
    src = rel.get("source", "?")
    tgt = rel.get("target", "?")
    if entities is not None:
        if isinstance(src, int) and 0 <= src < len(entities):
            src = entities[src].get("name", f"Entity {src}")
        if isinstance(tgt, int) and 0 <= tgt < len(entities):
            tgt = entities[tgt].get("name", f"Entity {tgt}")
    return f"{src} -> {tgt}"


def enforce_relationship_limits(
    relationships: list[dict[str, Any]],
    entity_count: int,
    *,
    max_relationship_ratio: float = 8.0,
    max_entity_degree: int = 25,
    max_same_source_type: int = 12,
    entities: list[dict[str, Any]] | None = None,
    filtering_log: FilteringLog | None = None,
    protect_orphans: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Enforce hard caps on relationship counts that LLMs may ignore in prompts.

    Keeps highest-confidence relationships when limits are exceeded.

    Caps enforced (in order):
    1. Same (source, type) pair: max ``max_same_source_type`` per pair
    2. Per-entity degree: max ``max_entity_degree`` per entity
    3. Total count: max ``max_relationship_ratio * entity_count``

    Args:
        relationships: Validated relationships (source/target are int indices).
        entity_count: Number of entities (for ratio cap).
        max_relationship_ratio: Max relationships as multiple of entity count.
        max_entity_degree: Max relationships per entity (source + target combined).
        max_same_source_type: Max relationships with same (source, type) pair.
        entities: Entity list for resolving index→name in filtering log.
        filtering_log: Optional log collector for pipeline diagnostics.
        protect_orphans: When True, the < 2 edges exception applies at the
            degree cap so minor entities keep at least one connection.  When
            False the exception is gated off; orphan endpoints lose their
            relationships at the cap like everyone else.  Mirrors the same
            toggle at the commit-time ``drop_orphan_entities`` site — both
            sites honor the same flag.

    Returns:
        Tuple of (capped_relationships, stats) where stats has keys
        ``total_before``, ``dropped_source_type``, ``dropped_degree``,
        ``dropped_total_cap``.

    """
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteredItem,
    )

    if not relationships or entity_count == 0:
        return relationships, {"total_before": len(relationships)}

    total_before = len(relationships)
    removed_items: list[FilteredItem] = []

    # Sort by confidence descending so we keep the best when dropping
    sorted_rels = sorted(
        relationships,
        key=lambda r: r.get("confidence", 0.0),
        reverse=True,
    )

    # Pass 1: Cap same (source, type) pairs
    source_type_counts: dict[tuple[int, str], int] = {}
    after_source_type: list[dict[str, Any]] = []
    dropped_source_type = 0

    for rel in sorted_rels:
        key = (rel.get("source", -1), rel.get("type", ""))
        count = source_type_counts.get(key, 0)
        if count >= max_same_source_type:
            dropped_source_type += 1
            _log_filtered(
                filtering_log,
                removed_items,
                item_type="relationship",
                name=_resolve_rel_name(rel, entities),
                entity_type=rel.get("type", "?"),
                reason=f"Same (source, type) cap exceeded ({max_same_source_type})",
                details={"pass": "source_type", "cap": max_same_source_type},
            )
            continue
        source_type_counts[key] = count + 1
        after_source_type.append(rel)

    # Pass 2: Cap per-entity degree
    entity_degrees: dict[int, int] = {}
    after_degree: list[dict[str, Any]] = []
    dropped_degree = 0

    for rel in after_source_type:
        src = rel.get("source", -1)
        tgt = rel.get("target", -1)
        src_deg = entity_degrees.get(src, 0)
        tgt_deg = entity_degrees.get(tgt, 0)
        if src_deg >= max_entity_degree or tgt_deg >= max_entity_degree:
            # Orphan protection: keep if either endpoint has < 2 edges,
            # ensuring minor entities get at least one connection.
            # Phase 7 audit-remediation (2026-05-09): now gated on the
            # protect_orphans toggle (was unconditional < 2 heuristic).
            if protect_orphans and (src_deg < 2 or tgt_deg < 2):
                entity_degrees[src] = src_deg + 1
                entity_degrees[tgt] = tgt_deg + 1
                after_degree.append(rel)
                continue
            dropped_degree += 1
            _log_filtered(
                filtering_log,
                removed_items,
                item_type="relationship",
                name=_resolve_rel_name(rel, entities),
                entity_type=rel.get("type", "?"),
                reason=f"Entity degree cap exceeded ({max_entity_degree})",
                details={"pass": "degree", "cap": max_entity_degree},
            )
            continue
        entity_degrees[src] = src_deg + 1
        entity_degrees[tgt] = tgt_deg + 1
        after_degree.append(rel)

    # Pass 3: Cap total count (with orphan protection)
    max_total = max(1, int(max_relationship_ratio * entity_count))
    # Separate orphan-protecting relationships (exempt from cap)
    protected: list[dict[str, Any]] = []
    countable: list[dict[str, Any]] = []
    for rel in after_degree:
        src = rel.get("source", -1)
        tgt = rel.get("target", -1)
        if entity_degrees.get(src, 0) < 2 or entity_degrees.get(tgt, 0) < 2:
            protected.append(rel)
        else:
            countable.append(rel)
    dropped_total_cap = max(0, len(countable) - max_total)
    if filtering_log is not None:
        removed_items.extend(
            FilteredItem(
                item_type="relationship",
                name=_resolve_rel_name(rel, entities),
                entity_type=rel.get("type", "?"),
                reason=f"Total cap exceeded ({max_total})",
                details={"pass": "total_cap", "cap": max_total},
            )
            for rel in countable[max_total:]
        )
    capped = protected + countable[:max_total]

    stats = {
        "total_before": total_before,
        "dropped_source_type": dropped_source_type,
        "dropped_degree": dropped_degree,
        "dropped_total_cap": dropped_total_cap,
    }

    total_dropped = dropped_source_type + dropped_degree + dropped_total_cap
    if total_dropped > 0:
        logger.info(
            "relationship_limits_enforced",
            **stats,
            total_after=len(capped),
        )

    if filtering_log is not None:
        filtering_log.add_stage(
            "relationship_limit_enforcement",
            input_count=total_before,
            removed_count=total_dropped,
            items=removed_items,
        )

    return capped, stats


def _parse_exclusion_examples(exclusion_rules: list[ExclusionRule]) -> set[str]:
    """Flatten exclusion rule examples into a lowercase match-set.

    Replaces the previous regex-from-natural-language parser. The new
    schema (``ExclusionRule``) guarantees ``examples`` is non-empty and
    ``description`` is non-blank at config load, so this function never
    silently degrades on a malformed rule.

    Args:
        exclusion_rules: Typed exclusion rules from a domain config.

    Returns:
        Set of lowercase example names across all rules.
    """
    return {ex.strip().lower() for rule in exclusion_rules for ex in rule.examples}


def filter_excluded_entities(
    entities: list[dict[str, Any]],
    exclusion_rules: list[ExclusionRule],
    filtering_log: FilteringLog | None = None,
) -> tuple[list[dict[str, Any]], dict[int, int | None]]:
    """Remove entities matching domain exclusion rules.

    Acts as a code-level safety net for when LLMs ignore prompt-level
    exclusion instructions. Walks the typed rule list, flattens the
    example sets, and drops entities whose name (or each alias) matches.

    Args:
        entities: List of entity dicts from extraction.
        exclusion_rules: Typed domain exclusion rules.
        filtering_log: Optional log collector for pipeline diagnostics.

    Returns:
        Tuple of (filtered_entities, index_mapping) where index_mapping
        maps old indices to new indices (or None if removed).

    """
    if not exclusion_rules or not entities:
        return entities, {i: i for i in range(len(entities))}

    excluded_names = _parse_exclusion_examples(exclusion_rules)
    if not excluded_names:
        return entities, {i: i for i in range(len(entities))}

    filtered: list[dict[str, Any]] = []
    index_mapping: dict[int, int | None] = {}
    removed_names: list[str] = []
    removed_items: list[FilteredItem] = []

    for old_idx, entity in enumerate(entities):
        name = (entity.get("name") or entity.get("label", "")).strip()
        name_lower = name.lower()

        if name_lower in excluded_names:
            index_mapping[old_idx] = None
            removed_names.append(name)
            _log_filtered(
                filtering_log,
                removed_items,
                item_type="entity",
                name=name,
                entity_type=entity.get("type", "?"),
                reason="Matched domain exclusion rule",
            )
            continue

        # Clean aliases: remove any that match exclusion patterns
        aliases = entity.get("aliases", [])
        if aliases:
            clean_aliases = [a for a in aliases if a.strip().lower() not in excluded_names]
            if len(clean_aliases) != len(aliases):
                cleaned = entity.copy()
                cleaned["aliases"] = clean_aliases
                new_idx = len(filtered)
                filtered.append(cleaned)
                index_mapping[old_idx] = new_idx
                continue

        new_idx = len(filtered)
        filtered.append(entity)
        index_mapping[old_idx] = new_idx

    if removed_names:
        logger.info(
            "excluded_entities_filtered",
            removed_count=len(removed_names),
            removed_names=removed_names,
        )

    if filtering_log is not None:
        filtering_log.add_stage(
            "entity_exclusion_filter",
            input_count=len(entities),
            removed_count=len(removed_names),
            items=removed_items,
        )

    return filtered, index_mapping


def deduplicate_relationships(
    relationships: list[dict[str, Any]],
    *,
    symmetric_types: frozenset[str] | None = None,
    inverse_map: dict[str, str] | None = None,
    filtering_log: FilteringLog | None = None,
) -> list[dict[str, Any]]:
    """Collapse duplicate relationships after entity merging (chunk-aware).

    Three-phase deduplication that preserves distinct cross-chunk interactions:
    1. Exact-match with chunk awareness: Within the same chunk_index, collapse
       (source, target, type) duplicates keeping highest confidence. Entries from
       different chunk_index values all survive.
    2. Symmetric with chunk awareness: For symmetric types, normalize direction
       and apply same chunk-aware logic.
    3. Inverse with chunk awareness: For inverse pairs, canonicalize direction
       and apply chunk-aware logic.

    Args:
        relationships: List of relationship dicts with source, target, type keys.
        symmetric_types: Frozenset of relationship type names that are symmetric
            (same type maps to itself as inverse). When provided, bidirectional
            duplicates like interacts_with(A,B) + interacts_with(B,A) are collapsed.
        inverse_map: Mapping of edge type to its inverse type (e.g. "parent_of" -> "child_of").
            When provided, both directions of an inverse pair are collapsed into one.
        filtering_log: Optional log collector for pipeline diagnostics.

    Returns:
        Deduplicated relationship list.

    """
    if not relationships:
        return relationships

    total_before = len(relationships)

    # Phase 1: Exact match with chunk awareness.
    # Key = (source, target, type, chunk_index). Within each key, keep highest confidence.
    result, exact_removed = _collapse_chunk_aware(relationships)

    if exact_removed > 0:
        logger.info(
            "exact_duplicate_relationships_removed",
            before=total_before,
            after=len(result),
            removed=exact_removed,
        )

    # Phase 2: Symmetric with chunk awareness.
    if symmetric_types:
        result, sym_removed = _collapse_symmetric_pairs(result, symmetric_types)
    else:
        sym_removed = 0

    # Phase 3: Inverse with chunk awareness.
    if inverse_map:
        result, inv_removed = _collapse_inverse_pairs(result, inverse_map)
    else:
        inv_removed = 0

    # Record all phases in filtering log
    total_removed = total_before - len(result)
    if filtering_log is not None and total_removed > 0:
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteredItem,
        )

        removed_items: list[FilteredItem] = []

        if exact_removed > 0:
            removed_items.append(
                FilteredItem(
                    item_type="relationship",
                    name=f"{exact_removed} exact duplicates (same chunk)",
                    entity_type="(various)",
                    reason="Exact (source, target, type) duplicate within same chunk",
                    details={"phase": "exact", "count": exact_removed},
                )
            )
        if sym_removed > 0:
            removed_items.append(
                FilteredItem(
                    item_type="relationship",
                    name=f"{sym_removed} symmetric duplicates (same chunk)",
                    entity_type="(various)",
                    reason="Bidirectional duplicate of symmetric type within same chunk",
                    details={"phase": "symmetric", "count": sym_removed},
                )
            )
        if inv_removed > 0:
            removed_items.append(
                FilteredItem(
                    item_type="relationship",
                    name=f"{inv_removed} inverse pairs (same chunk)",
                    entity_type="(various)",
                    reason="Inverse relationship pair collapsed within same chunk",
                    details={"phase": "inverse", "count": inv_removed},
                )
            )

        filtering_log.add_stage(
            "relationship_dedup",
            input_count=total_before,
            removed_count=total_removed,
            items=removed_items,
        )

    return result


def _collapse_chunk_aware(
    relationships: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Collapse exact (source, target, type) duplicates within the same chunk.

    Relationships from different chunks are preserved as distinct interactions.

    Args:
        relationships: Relationship list.

    Returns:
        Tuple of (deduplicated list, number removed).

    """
    seen: dict[tuple[Any, Any, str, Any], dict[str, Any]] = {}

    for rel in relationships:
        src = rel.get("source", rel.get("source_index"))
        tgt = rel.get("target", rel.get("target_index"))
        rtype = (rel.get("type") or "").strip().lower()
        chunk = rel.get("chunk_index")
        key = (src, tgt, rtype, chunk)

        if key in seen:
            existing = seen[key]
            if rel.get("confidence", 0.0) > existing.get("confidence", 0.0):
                seen[key] = rel
        else:
            seen[key] = rel

    removed = len(relationships) - len(seen)
    return list(seen.values()), removed


def _collapse_symmetric_pairs(
    relationships: list[dict[str, Any]],
    symmetric_types: frozenset[str],
) -> tuple[list[dict[str, Any]], int]:
    """Collapse bidirectional duplicates for symmetric types (chunk-aware).

    For symmetric types (e.g. interacts_with, spouse_of), (A, B) and (B, A)
    within the same chunk are semantically identical. Normalizes the pair key
    so both map to the same slot, keeping the highest-confidence one.
    Entries from different chunks are preserved.

    Args:
        relationships: Pre-deduplicated relationship list.
        symmetric_types: Frozenset of symmetric type names (lowercased).

    Returns:
        Tuple of (collapsed list, number removed).

    """
    sym_lower = frozenset(t.lower() for t in symmetric_types)
    final: dict[tuple[Any, Any, str, Any], dict[str, Any]] = {}

    for rel in relationships:
        # Coerce explicitly: ``X or ""`` would replace valid 0/false-y int IDs with "".
        src_raw = rel.get("source", rel.get("source_index"))
        tgt_raw = rel.get("target", rel.get("target_index"))
        src: str = "" if src_raw is None else str(src_raw)
        tgt: str = "" if tgt_raw is None else str(tgt_raw)
        rtype = (rel.get("type") or "").strip().lower()
        chunk = rel.get("chunk_index")

        # For symmetric types, normalize the pair so (A,B) == (B,A)
        if rtype in sym_lower:
            norm_src, norm_tgt = min(src, tgt), max(src, tgt)
        else:
            norm_src, norm_tgt = src, tgt
        key = (norm_src, norm_tgt, rtype, chunk)

        if key in final:
            existing = final[key]
            if rel.get("confidence", 0.0) > existing.get("confidence", 0.0):
                final[key] = rel
        else:
            final[key] = rel

    sym_removed = len(relationships) - len(final)
    if sym_removed > 0:
        logger.info(
            "symmetric_relationships_collapsed",
            before=len(relationships),
            after=len(final),
            removed=sym_removed,
        )

    return list(final.values()), sym_removed


def _collapse_inverse_pairs(
    relationships: list[dict[str, Any]],
    inverse_map: dict[str, str],
) -> tuple[list[dict[str, Any]], int]:
    """Collapse inverse relationship pairs into canonical direction (chunk-aware).

    For inverse pairs (e.g. parent_of/child_of), ``(A, parent_of, B)`` and
    ``(B, child_of, A)`` within the same chunk express the same fact. This
    function normalizes both to the canonical direction (alphabetically-first
    type name) and keeps the highest-confidence one per chunk.

    Args:
        relationships: Pre-deduplicated relationship list.
        inverse_map: Mapping of edge type to its inverse type.

    Returns:
        Tuple of (collapsed list, number removed).

    """
    if not inverse_map:
        return relationships, 0

    # Build bidirectional lookup (lowercased)
    inv_lower: dict[str, str] = {}
    for t1, t2 in inverse_map.items():
        inv_lower[t1.lower()] = t2.lower()
        inv_lower[t2.lower()] = t1.lower()

    # Determine canonical type for each inverse pair:
    # alphabetically-first type name is the canonical one
    def _canonical_key(
        src: Any,
        tgt: Any,
        rtype_lower: str,
        chunk: Any,
    ) -> tuple[Any, Any, str, Any]:
        inverse = inv_lower.get(rtype_lower)
        if inverse is None:
            return (src, tgt, rtype_lower, chunk)
        # Pick alphabetically-first type as canonical
        if rtype_lower <= inverse:
            return (src, tgt, rtype_lower, chunk)
        # Swap direction: use inverse type with flipped entities
        return (tgt, src, inverse, chunk)

    canonical: dict[tuple[Any, Any, str, Any], dict[str, Any]] = {}

    for rel in relationships:
        src = rel.get("source", rel.get("source_index"))
        tgt = rel.get("target", rel.get("target_index"))
        rtype = (rel.get("type") or "").strip().lower()
        chunk = rel.get("chunk_index")

        key = _canonical_key(src, tgt, rtype, chunk)

        if key in canonical:
            existing = canonical[key]
            if rel.get("confidence", 0.0) > existing.get("confidence", 0.0):
                canonical[key] = rel
        else:
            canonical[key] = rel

    inv_removed = len(relationships) - len(canonical)
    if inv_removed > 0:
        logger.info(
            "inverse_pair_relationships_collapsed",
            before=len(relationships),
            after=len(canonical),
            removed=inv_removed,
        )

    return list(canonical.values()), inv_removed


def _fuzzy_type_match(entity_type: str, allowed_types: list[str]) -> bool:
    """Check if an entity type matches any allowed type with fuzzy matching.

    Three-tier matching:
    1. Exact case-insensitive match
    2. Substring containment (either direction)
    3. Significant word overlap (ignoring stop words)

    Args:
        entity_type: The entity type to check.
        allowed_types: List of allowed type names.

    Returns:
        True if the entity type matches any allowed type.

    """
    if not entity_type or not allowed_types:
        return False

    type_lower = entity_type.strip().lower()

    # Tier 1: Exact case-insensitive
    for allowed in allowed_types:
        if type_lower == allowed.strip().lower():
            return True

    # Tier 2: Substring containment (either direction)
    for allowed in allowed_types:
        allowed_lower = allowed.strip().lower()
        if type_lower in allowed_lower or allowed_lower in type_lower:
            return True

    # Tier 3: Significant word overlap
    _type_stop_words = frozenset({"the", "a", "an", "of", "in", "on", "for", "and", "or"})
    type_words = {w for w in type_lower.split() if w not in _type_stop_words and len(w) > 1}
    if not type_words:
        return False

    for allowed in allowed_types:
        allowed_words = {
            w for w in allowed.strip().lower().split() if w not in _type_stop_words and len(w) > 1
        }
        if type_words & allowed_words:
            return True

    return False


# Stop words for relationship type word splitting (underscore-separated types)
_EDGE_TYPE_STOP_WORDS: frozenset[str] = frozenset(
    {"the", "a", "an", "of", "in", "on", "for", "and", "or", "is", "has"}
)


def _fuzzy_edge_type_lookup(
    rel_type: str,
    edge_type_constraints: dict[str, dict[str, list[str]]],
) -> str | None:
    """Find the best fuzzy match for a relationship type in the constraint dict.

    Same three-tier matching as ``_fuzzy_type_match`` but returns the matched
    key name instead of a bool, so the caller can look up constraints.

    Note: splits on underscores (relationship types use ``spouse_of``) unlike
    ``_fuzzy_type_match`` which splits on spaces (entity types use ``Literary Character``).

    Args:
        rel_type: Lowercased relationship type to look up.
        edge_type_constraints: Constraint dict keyed by canonical type names.

    Returns:
        Matched constraint key, or None if no match found.

    """
    if not rel_type or not edge_type_constraints:
        return None

    keys = list(edge_type_constraints.keys())

    # Tier 1: Exact (already handled by caller via dict.get, but included for completeness)
    for key in keys:
        if rel_type == key.strip().lower():
            return key

    # Tier 2: Substring containment (either direction)
    for key in keys:
        key_lower = key.strip().lower()
        if rel_type in key_lower or key_lower in rel_type:
            return key

    # Tier 3: Significant word overlap
    rel_words = {w for w in rel_type.split("_") if w not in _EDGE_TYPE_STOP_WORDS and len(w) > 1}
    if not rel_words:
        return None

    for key in keys:
        key_words = {
            w
            for w in key.strip().lower().split("_")
            if w not in _EDGE_TYPE_STOP_WORDS and len(w) > 1
        }
        if rel_words & key_words:
            return key

    return None


def validate_relationship_type_constraints(  # noqa: PLR0912, PLR0915
    relationships: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    edge_type_constraints: dict[str, dict[str, list[str]]],
    filtering_log: FilteringLog | None = None,
    *,
    strict_edge_type_constraints: bool = False,
    enable_direction_correction: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Validate relationships against edge template type constraints.

    For each relationship, checks that source and target entity types are
    allowed by the edge template's ``source_types``/``target_types`` constraints.
    Uses fuzzy matching to handle near-miss type names.

    Args:
        relationships: Validated relationships (source/target are int indices).
        entities: Entity list for type lookup.
        edge_type_constraints: Mapping of edge_type -> {"source_types": [...], "target_types": [...]}.
        filtering_log: Optional log collector for pipeline diagnostics.
        strict_edge_type_constraints: When True, drop relationships with types not
            matching any domain template. When False (default), unmatched types
            pass through without source/target constraint validation.
        enable_direction_correction: When True (default), swap source/target when
            the swap would satisfy type constraints (existing behavior). When False,
            drop the relationship instead of swapping. Counter increments either
            way so the wrong-direction LLM emission rate is always visible.

    Returns:
        Tuple of (filtered_relationships, stats) where stats has keys
        ``total_checked``, ``dropped_source_mismatch``, ``dropped_target_mismatch``,
        ``fuzzy_matched``, ``fell_through``, ``direction_corrected``.

    """
    if not relationships or not edge_type_constraints:
        return relationships, {
            "total_checked": len(relationships),
            "dropped_source_mismatch": 0,
            "dropped_target_mismatch": 0,
            "fuzzy_matched": 0,
            "fell_through": 0,
        }

    filtered: list[dict[str, Any]] = []
    dropped_source = 0
    dropped_target = 0
    fuzzy_matched = 0
    fell_through = 0
    direction_corrected = 0
    removed_items: list[FilteredItem] = []

    for rel in relationships:
        rel_type = (rel.get("type") or "").strip().lower()
        constraints = edge_type_constraints.get(rel_type)

        # No exact match — try fuzzy matching, then fall-through
        if not constraints:
            matched_key = _fuzzy_edge_type_lookup(rel_type, edge_type_constraints)
            if matched_key:
                constraints = edge_type_constraints[matched_key]
                fuzzy_matched += 1
                logger.info(
                    "relationship_type_fuzzy_matched",
                    original_type=rel_type,
                    matched_type=matched_key,
                )
            elif strict_edge_type_constraints:
                # Strict mode: drop unmatched types
                dropped_source += 1
                _log_filtered(
                    filtering_log,
                    removed_items,
                    item_type="relationship",
                    name=_resolve_rel_name(rel, entities),
                    entity_type=rel_type,
                    reason=f"Relationship type '{rel_type}' not in domain templates",
                    details={"rel_type": rel_type},
                )
                continue
            else:
                # Fall-through: let unmatched types pass without constraint check
                fell_through += 1
                logger.debug(
                    "relationship_type_fell_through",
                    rel_type=rel_type,
                )
                filtered.append(rel)
                continue

        # Look up source/target entity types
        source_idx = rel.get("source")
        target_idx = rel.get("target")

        source_type = ""
        target_type = ""
        if isinstance(source_idx, int) and 0 <= source_idx < len(entities):
            source_type = (entities[source_idx].get("type") or "").strip()
        if isinstance(target_idx, int) and 0 <= target_idx < len(entities):
            target_type = (entities[target_idx].get("type") or "").strip()

        # Check source/target type constraints with direction correction
        source_types = constraints.get("source_types", [])
        target_types = constraints.get("target_types", [])

        # Note: a missing entity type (source_type == "" or target_type == "")
        # used to short-circuit these checks to True, silently admitting any
        # relationship whose endpoint had no type. Now treated as a real
        # constraint failure so it surfaces via strict-mode drops or the
        # fell_through counter, never invisibly.
        source_ok = not source_types or _fuzzy_type_match(source_type, source_types)
        target_ok = not target_types or _fuzzy_type_match(target_type, target_types)

        if not source_ok or not target_ok:
            # Try swapping direction — LLM often gets source/target backwards
            swapped_source_ok = not source_types or _fuzzy_type_match(target_type, source_types)
            swapped_target_ok = not target_types or _fuzzy_type_match(source_type, target_types)

            if swapped_source_ok and swapped_target_ok:
                # The swap would fix the direction — always increment the
                # counter (measures wrong-direction LLM emission rate,
                # independent of the toggle).
                direction_corrected += 1
                if enable_direction_correction:
                    # Default (toggle on): swap and keep the relationship.
                    rel["source"], rel["target"] = rel["target"], rel["source"]
                    logger.info(
                        "relationship_direction_corrected",
                        rel_type=rel_type,
                        original_source=source_type,
                        original_target=target_type,
                    )
                else:
                    # Toggle off: drop instead of swapping.
                    logger.info(
                        "relationship_direction_correction_disabled_dropped",
                        rel_type=rel_type,
                        original_source=source_type,
                        original_target=target_type,
                    )
                    continue
            elif strict_edge_type_constraints:
                # Strict mode: drop
                if not source_ok:
                    dropped_source += 1
                    _log_filtered(
                        filtering_log,
                        removed_items,
                        item_type="relationship",
                        name=_resolve_rel_name(rel, entities),
                        entity_type=rel_type,
                        reason=f"Source type '{source_type}' not in allowed types",
                        details={"source_type": source_type, "allowed": source_types},
                    )
                else:
                    dropped_target += 1
                    _log_filtered(
                        filtering_log,
                        removed_items,
                        item_type="relationship",
                        name=_resolve_rel_name(rel, entities),
                        entity_type=rel_type,
                        reason=f"Target type '{target_type}' not in allowed types",
                        details={"target_type": target_type, "allowed": target_types},
                    )
                continue
            else:
                # Fall-through: keep with original direction
                fell_through += 1
                logger.debug(
                    "source_target_type_fell_through",
                    rel_type=rel_type,
                    source_type=source_type,
                    target_type=target_type,
                )

        filtered.append(rel)

    stats = {
        "total_checked": len(relationships),
        "dropped_source_mismatch": dropped_source,
        "dropped_target_mismatch": dropped_target,
        "fuzzy_matched": fuzzy_matched,
        "fell_through": fell_through,
        "direction_corrected": direction_corrected,
    }

    total_dropped = dropped_source + dropped_target
    if total_dropped > 0:
        logger.info(
            "relationship_type_constraints_enforced",
            **stats,
            total_after=len(filtered),
        )

    if filtering_log is not None:
        filtering_log.add_stage(
            "relationship_type_constraint",
            input_count=len(relationships),
            removed_count=total_dropped,
            items=removed_items,
        )
        if direction_corrected > 0:
            # ``removed_count`` carries the corrected count — not a removal, but
            # the only numeric field on _StageRecord. The stage name disambiguates.
            filtering_log.add_stage(
                "relationship_direction_corrected",
                input_count=len(relationships),
                removed_count=direction_corrected,
                items=[],
            )
        # Audit (2026-05-20): surface fuzzy-match rescue + fell-through
        # rates so the finalizer can promote them to source-row counters.
        # ``removed_count`` is again a carry field — stage name disambiguates.
        # Companions to ``relationship_type_constraint`` above; together they
        # describe every outcome of the type-constraint check.
        if fuzzy_matched > 0:
            filtering_log.add_stage(
                "relationship_type_fuzzy_matched",
                input_count=len(relationships),
                removed_count=fuzzy_matched,
                items=[],
            )
        if fell_through > 0:
            filtering_log.add_stage(
                "relationship_type_fell_through",
                input_count=len(relationships),
                removed_count=fell_through,
                items=[],
            )

    return filtered, stats


def clean_descriptor_aliases(
    entities: list[dict[str, Any]],
    title_words: frozenset[str] | None = None,
    filtering_log: FilteringLog | None = None,
) -> int:
    """Remove aliases that are descriptors rather than name variants.

    An alias should be an alternate form of the entity's name (e.g.,
    "Annette" for "Anna Pávlovna Schérer"). Aliases that share no
    significant word root with the entity name are likely descriptors
    injected by the LLM (e.g., "Nieces" for "Empress Márya Fëdorovna").

    Modifies entities in-place.

    Args:
        entities: Entity list (modified in-place).
        title_words: Title/honorific words to ignore when comparing.
        filtering_log: Optional log collector for pipeline diagnostics.

    Returns:
        Total number of aliases removed.

    """
    if not title_words:
        title_words = frozenset()

    # Also treat common articles as stop words for comparison
    stop_words = title_words | frozenset({"the", "a", "an", "of", "de", "von", "van", "le", "la"})

    total_removed = 0

    for entity in entities:
        aliases = entity.get("aliases", [])
        if not aliases:
            continue

        name = entity.get("name") or entity.get("label", "")
        name_words = _significant_words(name, stop_words)
        if not name_words:
            continue

        # Two-pass alias validation:
        # Pass 1: accept aliases that share a word root with the entity name
        # Pass 2: accept remaining aliases that share a root with accepted aliases
        # This handles cases like "Count Bezukhov" for entity "Pierre" —
        # it passes via "Pierre Bezukhov" which was accepted in pass 1.
        accepted: list[str] = []
        accepted_words: set[str] = set(name_words)
        deferred: list[tuple[str, set[str]]] = []

        for alias in aliases:
            alias_words = _significant_words(alias, stop_words)

            if not alias_words:
                total_removed += 1
                logger.debug(
                    "descriptor_alias_removed",
                    alias=alias,
                    entity_name=name,
                    reason="no_significant_words",
                )
                continue

            if _shares_word_root(alias_words, name_words):
                accepted.append(alias)
                accepted_words.update(alias_words)
            else:
                deferred.append((alias, alias_words))

        # Pass 2: check deferred aliases against accepted word pool
        rejected: list[str] = []
        for alias, alias_words in deferred:
            if _shares_word_root(alias_words, accepted_words):
                accepted.append(alias)
                accepted_words.update(alias_words)
            else:
                rejected.append(alias)
                total_removed += 1
                logger.info(
                    "descriptor_alias_removed",
                    alias=alias,
                    entity_name=name,
                    reason="no_name_overlap",
                )

        if len(accepted) != len(aliases):
            entity["aliases"] = accepted

    if filtering_log is not None and total_removed > 0:
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteredItem,
        )

        # Summary item — individual aliases aren't distinct items
        total_aliases = sum(len(e.get("aliases", [])) for e in entities) + total_removed
        filtering_log.add_stage(
            "descriptor_alias_cleaning",
            input_count=total_aliases,
            removed_count=total_removed,
            items=[
                FilteredItem(
                    item_type="entity",
                    name=f"{total_removed} descriptor aliases",
                    entity_type="(various)",
                    reason="Alias has no word-root overlap with entity name",
                    details={"total_removed": total_removed},
                )
            ],
        )

    return total_removed


def filter_implausible_entities(
    entities: list[dict[str, Any]],
    source_sentences: list[str],
    named_referent_types: set[str] | None = None,
    threshold: float = 0.40,
    threshold_non_named: float = 0.15,
    filtering_log: FilteringLog | None = None,
) -> tuple[list[dict[str, Any]], dict[int, int | None]]:
    """Remove entities with implausible names using a composite plausibility score.

    Detects LLM-hallucinated entities like "The Armchair" or "The Sword"
    using five lightweight heuristic signals that don't require an LLM call.

    Signals (weighted):
    - has_proper_noun (0.15): Name contains a capitalized word that isn't a
      common English word.
    - low_func_ratio (0.15): Name has a low ratio of function words.
    - not_descriptive (0.20): Name doesn't match "The <abstract>" patterns.
    - reasonable_length (0.15): Name is 1-6 words.
    - grounded_in_source (0.35): For named types, significant words appear
      capitalized mid-sentence in source text (not just at sentence start).

    The filter is type-aware: ``named_referent_types`` specifies which entity
    types MUST have plausible proper names (e.g., Character, Author). Types
    not in this set are only filtered if their score falls below
    ``threshold_non_named``.

    Args:
        entities: Entity list from extraction.
        source_sentences: The numbered sentences fed to the LLM.
        named_referent_types: Entity types that require proper names. If None,
            the filter applies uniformly to all types.
        threshold: Score below which named-type entities are rejected (default 0.40).
        threshold_non_named: Score below which non-named-type entities are
            rejected (default 0.15).
        filtering_log: Optional log collector for pipeline diagnostics.

    Returns:
        Tuple of (filtered_entities, index_mapping).

    """
    if not entities:
        return entities, {i: i for i in range(len(entities))}

    # Pre-join source text for grounding checks (both original and lowercase)
    source_text_original = " ".join(source_sentences)
    source_text_lower = source_text_original.lower()
    named_lower = {t.lower() for t in named_referent_types} if named_referent_types else None

    filtered: list[dict[str, Any]] = []
    index_mapping: dict[int, int | None] = {}
    removed: list[tuple[str, str, float]] = []
    removed_items: list[FilteredItem] = []

    for old_idx, entity in enumerate(entities):
        name = (entity.get("name") or "").strip()
        entity_type = (entity.get("type") or "").strip()

        if not name:
            index_mapping[old_idx] = None
            removed.append((name, entity_type, 0.0))
            _log_filtered(
                filtering_log,
                removed_items,
                item_type="entity",
                name="(empty)",
                entity_type=entity_type,
                reason="Empty name",
                details={"score": 0.0},
            )
            continue

        # Type-aware thresholding
        is_named_type = named_lower is None or entity_type.lower() in named_lower

        score = _name_plausibility_score(
            name, source_text_lower, source_text_original, is_named_type
        )

        effective_threshold = threshold if is_named_type else threshold_non_named

        if score < effective_threshold:
            index_mapping[old_idx] = None
            removed.append((name, entity_type, score))
            _log_filtered(
                filtering_log,
                removed_items,
                item_type="entity",
                name=name,
                entity_type=entity_type,
                reason=f"Plausibility score {score:.2f} < {effective_threshold:.2f}",
                details={"score": round(score, 3), "threshold": effective_threshold},
            )
        else:
            new_idx = len(filtered)
            filtered.append(entity)
            index_mapping[old_idx] = new_idx

    if removed:
        logger.info(
            "implausible_entities_filtered",
            removed_count=len(removed),
            removed=[{"name": n, "type": t, "score": round(s, 2)} for n, t, s in removed],
        )

    if filtering_log is not None:
        filtering_log.add_stage(
            "implausible_entity_filter",
            input_count=len(entities),
            removed_count=len(removed),
            items=removed_items,
        )

    return filtered, index_mapping


# Common English words that don't count as proper nouns even when capitalized
_COMMON_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "and",
        "or",
        "but",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "his",
        "her",
        "its",
        "their",
        "my",
        "your",
        "our",
        "this",
        "that",
        "these",
        "those",
        "it",
        "he",
        "she",
        "they",
        "not",
        "no",
        "by",
        "from",
        "as",
        "between",
        "about",
        "state",
        "relationship",
        "feeling",
        "emotional",
        "feelings",
        "situation",
        "condition",
        "nature",
        "quality",
        "aspect",
    }
)

# Sentence-ending punctuation followed by space (used for sentence-start detection)
_SENTENCE_ENDS = frozenset({". ", "! ", "? "})

# Function words that signal a descriptive phrase rather than a proper name
_FUNCTION_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "and",
        "or",
        "but",
        "between",
        "about",
        "from",
        "by",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "his",
        "her",
        "its",
        "their",
        "my",
        "your",
        "our",
    }
)


def _compute_descriptive_signal(
    words: list[str],
    word_count: int,
    is_named_type: bool,
    source_text_lower: str,
    source_text_original: str,
) -> float:
    """Compute the not-descriptive signal for plausibility scoring.

    Detects "The <abstract> ..." patterns that indicate hallucinated entities.
    For 2-word "The X" names on named types, checks source grounding.

    Args:
        words: Tokenized entity name.
        word_count: Number of words.
        is_named_type: Whether the entity type requires a proper name.
        source_text_lower: Lowercased source text.
        source_text_original: Original-case source text.

    Returns:
        1.0 if name looks like a real name, 0.0 if descriptive.

    """
    not_descriptive = 1.0

    # 3+ word "The <adj/abstract> ..." pattern
    if word_count >= 3 and words[0].lower() == "the":
        second = words[1].lower().strip(".,;:!?\"'()-")
        if (
            not second[0:1].isupper()
            or second in _COMMON_WORDS
            or any(w.lower() in {"between", "of", "in", "about", "from"} for w in words[2:4])
        ):
            not_descriptive = 0.0

    # 2-word "The X" for named types: check source grounding of second word
    if word_count == 2 and words[0].lower() == "the" and is_named_type:
        second_word = words[1].lower().strip(".,;:!?\"'()-")
        if second_word and len(second_word) > 1:
            cap_result = _has_mid_sentence_cap(second_word, source_text_lower, source_text_original)
            if cap_result in ("lower", "start"):
                not_descriptive = 0.0

    # Multi-word names with no uppercase non-common words at all
    if word_count >= 2 and not any(
        w[0:1].isupper() and w.lower().strip(".,;:!?\"'()-") not in _COMMON_WORDS for w in words
    ):
        not_descriptive = 0.0

    return not_descriptive


def _compute_grounding_signal(
    words: list[str],
    is_named_type: bool,
    source_text_lower: str,
    source_text_original: str,
) -> float:
    """Compute the source-grounding signal for plausibility scoring.

    For named entity types, checks whether significant words from the name
    appear capitalized mid-sentence in source text. Common nouns promoted
    by the LLM (armchair, sword) only appear lowercase; real entities
    (Pierre, Napoleon) appear capitalized.

    Args:
        words: Tokenized entity name.
        is_named_type: Whether the entity type requires a proper name.
        source_text_lower: Lowercased source text.
        source_text_original: Original-case source text.

    Returns:
        1.0 if grounded or not applicable, 0.5 if found capitalized at
        sentence starts (partial evidence of proper usage), 0.0 if found
        only in lowercase (likely a common noun).

    """
    if not is_named_type:
        return 1.0

    significant = [
        w.lower().strip(".,;:!?\"'()-")
        for w in words
        if w.lower().strip(".,;:!?\"'()-") not in _FUNCTION_WORDS
        and len(w.strip(".,;:!?\"'()-")) > 1
    ]
    if not significant:
        return 1.0

    best = "none"
    for word_lower in significant:
        cap_result = _has_mid_sentence_cap(word_lower, source_text_lower, source_text_original)
        if cap_result == "mid":
            return 1.0  # One capitalized mid-sentence word is enough
        # Track the best evidence seen so far: start > lower > none
        if cap_result == "start" and best != "start":
            best = "start"
        elif cap_result == "lower" and best == "none":
            best = "lower"

    if best == "start":
        # Word appears capitalized at sentence starts — partial evidence
        # of proper usage (e.g., titles/designations like "Abbe").
        return 0.5
    if best == "lower":
        # Word found only lowercase — likely a common noun promoted
        # by the LLM (e.g., "armchair", "sword").
        return 0.0
    # Word not found at all — benefit of the doubt.
    return 1.0


def _name_plausibility_score(
    name: str,
    source_text_lower: str,
    source_text_original: str,
    is_named_type: bool,
) -> float:
    """Compute a 0-1 plausibility score for an entity name.

    Five weighted signals:
    - has_proper (0.15): Contains a capitalized non-common word.
    - low_func_ratio (0.15): Low ratio of function words.
    - not_descriptive (0.20): Not a "The <abstract>" pattern.
    - reasonable_length (0.15): 1-6 words.
    - grounded_in_source (0.35): For named types, significant words appear
      capitalized mid-sentence in source text.

    Args:
        name: The entity name to evaluate.
        source_text_lower: Lowercased joined source sentences.
        source_text_original: Original-case joined source sentences.
        is_named_type: Whether this entity's type requires a proper name.

    Returns:
        Weighted score between 0.0 and 1.0.

    """
    words = name.split()
    word_count = len(words)

    if word_count == 0:
        return 0.0

    # Signal 1: Has proper noun (0.15)
    has_proper = 0.0
    for word in words:
        cleaned = word.strip(".,;:!?\"'()-")
        if (
            cleaned
            and cleaned[0].isupper()
            and cleaned.lower() not in _COMMON_WORDS
            and len(cleaned) > 1
        ):
            has_proper = 1.0
            break

    # Signal 2: Low function-word ratio (0.15)
    func_count = sum(1 for w in words if w.lower().strip(".,;:!?\"'()-") in _FUNCTION_WORDS)
    func_ratio = func_count / word_count
    if func_ratio <= 0.2:
        low_func_ratio = 1.0
    elif func_ratio <= 0.35:
        low_func_ratio = 0.5
    else:
        low_func_ratio = 0.0

    # Signal 3: Not a descriptive phrase (0.20)
    not_descriptive = _compute_descriptive_signal(
        words, word_count, is_named_type, source_text_lower, source_text_original
    )

    # Signal 4: Reasonable length (0.15)
    if 1 <= word_count <= 4:
        reasonable_length = 1.0
    elif word_count <= 6:
        reasonable_length = 0.5
    else:
        reasonable_length = 0.0

    # Signal 5: Grounded in source (0.35)
    grounded = _compute_grounding_signal(
        words, is_named_type, source_text_lower, source_text_original
    )

    # Hard gate: if the name is a descriptive phrase ("The X of Y"),
    # cap the score regardless of embedded proper nouns.
    if not_descriptive == 0.0 and word_count >= 4:
        return max(0.0, low_func_ratio * 0.15 + reasonable_length * 0.15)

    # Hard gate: for named-referent types, if ALL significant words only
    # appear lowercase in source text, this is a common noun phrase being
    # promoted (e.g., "Sitting Room", "Inkstand"), not a proper name.
    # Cap the score to prevent it from passing the threshold.
    if grounded == 0.0 and is_named_type:
        return has_proper * 0.15

    # Weighted composite
    return (
        has_proper * 0.15
        + low_func_ratio * 0.15
        + not_descriptive * 0.20
        + reasonable_length * 0.15
        + grounded * 0.35
    )


def _has_mid_sentence_cap(
    word_lower: str,
    source_text_lower: str,
    source_text_original: str,
) -> str:
    """Check how a word appears in source text regarding capitalization.

    Sentence-start positions (after ". ", "! ", "? ", or position 0) are
    excluded from mid-sentence checks since any word is capitalized there
    regardless of being a proper noun.

    Args:
        word_lower: The word to check (lowercase).
        source_text_lower: Lowercased source text for position finding.
        source_text_original: Original-case source text.

    Returns:
        ``"mid"`` if found capitalized mid-sentence (strong evidence of
        proper noun), ``"start"`` if found capitalized only at sentence
        starts (partial evidence), ``"lower"`` if found only in lowercase,
        ``"none"`` if word not found at all.

    """
    if not word_lower or not source_text_lower:
        return "none"

    found = False
    found_at_sentence_start = False
    start = 0
    word_len = len(word_lower)

    while True:
        pos = source_text_lower.find(word_lower, start)
        if pos == -1:
            break

        # Check it's a whole word (not a substring of another word)
        if pos > 0 and source_text_lower[pos - 1].isalpha():
            start = pos + 1
            continue
        end_pos = pos + word_len
        if end_pos < len(source_text_lower) and source_text_lower[end_pos].isalpha():
            start = pos + 1
            continue

        found = True

        # Check if capitalized in original text
        original_char = source_text_original[pos]
        if original_char.isupper():
            # Exclude sentence-start positions
            is_sentence_start = pos == 0
            if not is_sentence_start and pos >= 2:
                preceding = source_text_original[pos - 2 : pos]
                if preceding in _SENTENCE_ENDS:
                    is_sentence_start = True
            if not is_sentence_start:
                return "mid"
            found_at_sentence_start = True

        start = pos + 1

    if not found:
        return "none"
    return "start" if found_at_sentence_start else "lower"


def _significant_words(name: str, stop_words: frozenset[str]) -> set[str]:
    """Extract significant words from a name, stripping diacritics and stop words.

    Args:
        name: Name string.
        stop_words: Words to exclude (titles, articles, prepositions).

    Returns:
        Set of significant lowercase words (length > 1).

    """
    import unicodedata

    # Strip diacritics
    nfkd = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    normalized = stripped.lower().strip()

    # Tokenize on common separators
    for sep in ".,/-_":
        normalized = normalized.replace(sep, " ")

    words = set()
    for word in normalized.split():
        w = word.strip()
        if w and w not in stop_words and len(w) > 1:
            words.add(w)
    return words


def _shares_word_root(
    words_a: set[str], words_b: set[str], min_prefix: int = 4, min_coverage: float = 0.5
) -> bool:
    """Check if any word in set A shares a prefix with any word in set B.

    A match requires the shared prefix to be at least ``min_prefix`` characters
    AND cover at least ``min_coverage`` of the shorter word's length. This
    prevents short prefixes from matching unrelated long words (e.g. "pre" in
    "president" vs "prehistoric").

    Args:
        words_a: First word set.
        words_b: Second word set.
        min_prefix: Minimum shared prefix length.
        min_coverage: Minimum fraction of the shorter word the prefix must cover.

    Returns:
        True if any word pair shares a qualifying prefix.

    """
    for wa in words_a:
        for wb in words_b:
            # Find actual shared prefix length
            max_possible = min(len(wa), len(wb))
            prefix_len = 0
            for i in range(max_possible):
                if wa[i] == wb[i]:
                    prefix_len = i + 1
                else:
                    break
            shorter_len = min(len(wa), len(wb))
            if (
                prefix_len >= min_prefix
                and shorter_len > 0
                and prefix_len / shorter_len >= min_coverage
            ):
                return True
    return False
