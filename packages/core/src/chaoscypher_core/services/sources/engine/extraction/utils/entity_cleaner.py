# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Entity cleanup and validation utilities.

Standalone functions for cleaning, validating, and transforming entity
and relationship data extracted by the AI pipeline.

Functions:
- apply_properties_to_entities: Attach parsed P| properties to their entities
- validate_relationships: Check bounds, reject self-loops, optionally resolve names
- parse_index: Parse a source/target value as an integer index
- filter_excluded_entities: Keep, flag or drop entities matching domain exclusion rules
- deduplicate_relationships: Chunk-aware dedup of (source, target, type) relationships
- RelationshipOrientation / orient_relationships: the canonical form of a relationship fact
  (symmetric endpoints ordered, inverse pairs on one label), shared with the commit
- clean_descriptor_aliases: Move aliases that are descriptors, not name variants, to descriptors
- move_relation_descriptors: Move aliases naming another person's relation to descriptors
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        NameConventions,
    )
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


CAP_PASSES: tuple[str, ...] = ("source_type", "degree", "total")
"""The relationship caps, in the order :func:`enforce_relationship_limits` applies them."""

CAP_STAGE_COUNTS: tuple[str, ...] = tuple(f"capped_{p}" for p in CAP_PASSES)
"""The ``relationship_limit_enforcement`` stage's counts: the edges each cap kept flagged.

Until 2026-10-07 the same counts were the edges each cap dropped, named
``dropped_source_type``, ``dropped_degree`` and ``dropped_total_cap``."""


def capped_relationships(stages: Iterable[Mapping[str, Any]]) -> int:
    """The relationships the caps kept flagged, over a filtering log's ``stages`` (serialized).

    What the source row's ``relationships_dropped_capped`` counts since
    2026-10-07 (the extraction finalizer reads it): the
    ``relationship_limit_enforcement`` stages' :data:`CAP_STAGE_COUNTS`.
    Until then the stage's ``removed_count``, the edges the caps dropped.
    """
    return sum(
        int((stage.get("counts") or {}).get(name, 0) or 0)
        for stage in stages
        if stage.get("stage") == "relationship_limit_enforcement"
        for name in CAP_STAGE_COUNTS
    )


def flag_capped(rel: Mapping[str, Any], cap_pass: str, cap: int) -> dict[str, Any]:
    """``rel`` kept past a relationship cap: a copy flagged ``capped`` (``name_identity.CAPPED_KEY``).

    The one writer of the edge flag. The commit stores ``properties`` on the
    edge, so the walk weighs it at ``CAPPED_EDGE_WEIGHT``
    (``name_identity.edge_walk_weight``) and the canvas draws it thin; which
    cap and its value are kept beside it (``cap_pass``, ``cap``).
    """
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        CAPPED_KEY,
        with_pipeline_flag,
    )

    stored = rel.get("properties")
    props = with_pipeline_flag(
        stored if isinstance(stored, Mapping) else None,
        CAPPED_KEY,
        on=True,
        cap_pass=cap_pass,
        cap=cap,
    )
    return {**rel, "properties": props}


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
    """Flag the relationships past the hard caps LLMs may ignore in prompts; drop none.

    Keep, flag, never delete (2026-10-07): a relationship past a cap is kept,
    flagged ``capped`` (:func:`flag_capped`), so the walk ranks it last and the
    canvas draws it thin. Until then the caps dropped it, justification and
    all. The highest-confidence relationships are the ones within the caps;
    a flagged one counts toward no later cap (a flagged edge does not make a
    hub).

    Caps applied (in order):
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
            degree cap so minor entities keep at least one connection within
            it.  When False the exception is gated off.  Mirrors the same
            toggle at the commit-time ``flag_orphan_entities`` site — both
            sites honor the same flag.

    Returns:
        Tuple of (relationships, stats): every relationship, the ones within
        the caps first (by confidence) and the flagged ones after, and stats
        with keys ``total_before`` and :data:`CAP_STAGE_COUNTS`.

    """
    if not relationships or entity_count == 0:
        return relationships, {"total_before": len(relationships)}

    total_before = len(relationships)
    flagged_items: list[FilteredItem] = []
    capped_rels: list[dict[str, Any]] = []
    counts = dict.fromkeys(CAP_PASSES, 0)

    def _cap(rel: dict[str, Any], cap_pass: str, cap: int, reason: str) -> None:
        counts[cap_pass] += 1
        capped_rels.append(flag_capped(rel, cap_pass, cap))
        _log_filtered(
            filtering_log,
            flagged_items,
            item_type="relationship",
            name=_resolve_rel_name(rel, entities),
            entity_type=rel.get("type", "?"),
            reason=f"{reason}: kept flagged capped",
            details={"pass": cap_pass, "cap": cap, "kept": True},
        )

    # Sort by confidence descending so the best are the ones within the caps
    sorted_rels = sorted(
        relationships,
        key=lambda r: r.get("confidence", 0.0),
        reverse=True,
    )

    # Pass 1: Cap same (source, type) pairs
    source_type_counts: dict[tuple[int, str], int] = {}
    after_source_type: list[dict[str, Any]] = []

    for rel in sorted_rels:
        key = (rel.get("source", -1), rel.get("type", ""))
        count = source_type_counts.get(key, 0)
        if count >= max_same_source_type:
            _cap(
                rel,
                "source_type",
                max_same_source_type,
                f"Same (source, type) cap exceeded ({max_same_source_type})",
            )
            continue
        source_type_counts[key] = count + 1
        after_source_type.append(rel)

    # Pass 2: Cap per-entity degree
    entity_degrees: dict[int, int] = {}
    after_degree: list[dict[str, Any]] = []

    for rel in after_source_type:
        src = rel.get("source", -1)
        tgt = rel.get("target", -1)
        src_deg = entity_degrees.get(src, 0)
        tgt_deg = entity_degrees.get(tgt, 0)
        if src_deg >= max_entity_degree or tgt_deg >= max_entity_degree:
            # Orphan protection: within the cap if either endpoint has < 2 edges,
            # ensuring minor entities get at least one connection.
            # Phase 7 audit-remediation (2026-05-09): now gated on the
            # protect_orphans toggle (was unconditional < 2 heuristic).
            if protect_orphans and (src_deg < 2 or tgt_deg < 2):
                entity_degrees[src] = src_deg + 1
                entity_degrees[tgt] = tgt_deg + 1
                after_degree.append(rel)
                continue
            _cap(
                rel,
                "degree",
                max_entity_degree,
                f"Entity degree cap exceeded ({max_entity_degree})",
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
    for rel in countable[max_total:]:
        _cap(rel, "total", max_total, f"Total cap exceeded ({max_total})")
    kept = protected + countable[:max_total] + capped_rels

    stats = {
        "total_before": total_before,
        **{f"capped_{p}": n for p, n in counts.items()},
    }

    total_capped = len(capped_rels)
    if total_capped > 0:
        logger.info(
            "relationship_limits_enforced",
            **stats,
            total_after=len(kept),
        )

    if filtering_log is not None:
        filtering_log.add_stage(
            "relationship_limit_enforcement",
            input_count=total_before,
            removed_count=0,
            items=flagged_items,
            counts={k: v for k, v in stats.items() if k != "total_before"},
        )

    return kept, stats


def carries_evidence(entity: Mapping[str, Any]) -> bool:
    """Whether a record carries what the text says of it: a description or an evidence sentence.

    The one reading of "has evidence" behind keep, flag, never delete
    (2026-10-07): a record that carries evidence is never dropped by the
    pipeline, it is kept with a flag and its consumers decide; a record that
    carries nothing may be dropped, counted. A description is any non-blank
    ``description``; an evidence sentence is an ``evidence`` entry (a dict, as
    ``orchestration.stamp_group_evidence`` stamps them) whose ``text`` is
    not blank. A cited chunk id alone is no sentence. Read by the commit's
    orphan rule (``commit.service.flag_orphan_entities``) and the name
    filters' reference rule (:func:`kept_as_reference`).
    """
    if str(entity.get("description") or "").strip():
        return True
    evidence = entity.get("evidence")
    if not isinstance(evidence, list):
        return False
    return any(isinstance(e, Mapping) and str(e.get("text") or "").strip() for e in evidence)


def kept_as_reference(
    entity: Mapping[str, Any], conventions: NameConventions | None = None
) -> bool:
    """A record written under a reference that says something of its own: never dropped by a name filter.

    ``the count``, ``the vicomte``, ``Prince Andrew's wife``: a bare title, a
    role or a descriptor names no one, and ``the army``, ``the government``,
    ``the village`` name no group, place or event
    (``name_identity.labelled_by_reference``, every type since 2026-10-07),
    but the mention's evidence (:func:`carries_evidence`) or properties are
    what the text says of it. The per-chunk name filters (the domain's
    exclusion examples, the plausibility score) keep it, and the
    deduplication folds it into the record its own names identify or keeps it
    flagged (``name_identity.DESCRIPTOR_REFERENCE_KEY``). Until 2026-10-07
    both filters dropped a person's: the v4 generic arm lost three of its four
    ``the count`` mentions to the plausibility score (the one kept, the record
    holding the Archives fact a benchmark question needs, survived only
    because its type column was empty), and every literary ``the vicomte``,
    ``the little princess`` and ``the countess`` mention but the few with a
    high score; a group's (``the army``) went to the named-referent bar.
    """
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        DEFAULT_NAME_CONVENTIONS,
        PIPELINE_WRITTEN_KEYS,
        labelled_by_reference,
    )

    name = str(entity.get("name") or entity.get("label") or "").strip()
    conv = conventions or DEFAULT_NAME_CONVENTIONS
    if not labelled_by_reference(name, conv, entity.get("type")):
        return False
    props = entity.get("properties")
    # what the text says of it: a flag the pipeline wrote (unplaced_type, ...) is none
    said = isinstance(props, Mapping) and any(k not in PIPELINE_WRITTEN_KEYS for k in props)
    return carries_evidence(entity) or said


def flag_low_plausibility(entity: Mapping[str, Any], score: float) -> dict[str, Any]:
    """``entity`` kept below the plausibility bar: a copy flagged ``low_plausibility``, its score beside it.

    The one writer of the plausibility flag (``name_identity.LOW_PLAUSIBILITY_KEY``,
    the score under ``PLAUSIBILITY_SCORE_KEY``), for an entity that carries
    evidence (:func:`carries_evidence`).
    """
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        LOW_PLAUSIBILITY_KEY,
        PLAUSIBILITY_SCORE_KEY,
        with_pipeline_flag,
    )

    stored = entity.get("properties")
    props = with_pipeline_flag(
        stored if isinstance(stored, Mapping) else None,
        LOW_PLAUSIBILITY_KEY,
        on=True,
        **{PLAUSIBILITY_SCORE_KEY: round(score, 3)},
    )
    return {**entity, "properties": props}


def _exclusion_examples(exclusion_rules: list[ExclusionRule]) -> dict[str, ExclusionRule]:
    """Every exclusion example, lowercased, with the first rule that lists it.

    Replaces the previous regex-from-natural-language parser. The schema
    (``ExclusionRule``) guarantees ``examples`` is non-empty and
    ``description`` is non-blank at config load, so this never silently
    degrades on a malformed rule.
    """
    found: dict[str, ExclusionRule] = {}
    for rule in exclusion_rules:
        for example in rule.examples:
            found.setdefault(example.strip().lower(), rule)
    return found


def flag_excluded_name(entity: Mapping[str, Any], rule: ExclusionRule) -> dict[str, Any]:
    """``entity`` kept although its name is an exclusion example: a copy flagged ``excluded_name``.

    The one writer of the exclusion flag (``name_identity.EXCLUDED_NAME_KEY``,
    the matching rule's description under ``EXCLUDED_BY_KEY``).
    """
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        EXCLUDED_BY_KEY,
        EXCLUDED_NAME_KEY,
        with_pipeline_flag,
    )

    stored = entity.get("properties")
    props = with_pipeline_flag(
        stored if isinstance(stored, Mapping) else None,
        EXCLUDED_NAME_KEY,
        on=True,
        **{EXCLUDED_BY_KEY: rule.description},
    )
    return {**entity, "properties": props}


EXCLUDED_NAME_STAGE_COUNT = "excluded_name_kept"
"""The ``entity_exclusion_filter`` stage's count of the entities kept flagged ``excluded_name``."""


def filter_excluded_entities(
    entities: list[dict[str, Any]],
    exclusion_rules: list[ExclusionRule],
    filtering_log: FilteringLog | None = None,
    conventions: NameConventions | None = None,
) -> tuple[list[dict[str, Any]], dict[int, int | None]]:
    """Apply the domain's exclusion rules to one chunk's entities: keep, flag, never delete.

    A code-level safety net for when the model writes a name the prompt's
    exclusions list (every shipped exclusion names a record that carries no
    fact: pronouns, page furniture, footnote markers;
    ``test_prompt_content_guards`` holds them to that). The one decision:

    - a name no exclusion example matches is kept as written (an alias that
      matches one is removed from its aliases);
    - a matched name that carries evidence (:func:`carries_evidence`: a
      description), or a reference that says something of its own
      (:func:`kept_as_reference`: ``the king`` with a description), is kept,
      flagged ``excluded_name`` with the matching rule beside it
      (:func:`flag_excluded_name`); the deduplication still folds or flags a
      reference (keep, flag, never delete, 2026-10-08: until then a
      non-person excluded name was dropped, description and all);
    - a matched name carrying nothing is dropped.

    Args:
        entities: List of entity dicts from extraction.
        exclusion_rules: Typed domain exclusion rules.
        filtering_log: Optional log collector for pipeline diagnostics.
        conventions: The domain's naming conventions (what a reference is).

    Returns:
        Tuple of (filtered_entities, index_mapping) where index_mapping
        maps old indices to new indices (or None if removed). The stage's
        ``removed_count`` counts the drops and its ``counts`` the flags
        (:data:`EXCLUDED_NAME_STAGE_COUNT`).

    """
    if not exclusion_rules or not entities:
        return entities, {i: i for i in range(len(entities))}

    excluded_names = _exclusion_examples(exclusion_rules)
    if not excluded_names:
        return entities, {i: i for i in range(len(entities))}

    filtered: list[dict[str, Any]] = []
    index_mapping: dict[int, int | None] = {}
    removed_names: list[str] = []
    flagged_names: list[str] = []
    items: list[FilteredItem] = []

    for old_idx, entity in enumerate(entities):
        name = (entity.get("name") or entity.get("label", "")).strip()
        rule = excluded_names.get(name.lower())
        if rule is not None and not (
            carries_evidence(entity) or kept_as_reference(entity, conventions)
        ):
            index_mapping[old_idx] = None
            removed_names.append(name)
            _log_filtered(
                filtering_log,
                items,
                item_type="entity",
                name=name,
                entity_type=entity.get("type", "?"),
                reason="Matched domain exclusion rule",
                details={"rule": rule.description},
            )
            continue

        kept = entity
        if rule is not None:
            kept = flag_excluded_name(entity, rule)
            flagged_names.append(name)
            _log_filtered(
                filtering_log,
                items,
                item_type="entity",
                name=name,
                entity_type=entity.get("type", "?"),
                reason="Matched domain exclusion rule: kept flagged (carries evidence)",
                details={"rule": rule.description, "kept": True},
            )

        # Clean aliases: remove any that match exclusion patterns
        aliases = kept.get("aliases", [])
        if aliases:
            clean_aliases = [a for a in aliases if a.strip().lower() not in excluded_names]
            if len(clean_aliases) != len(aliases):
                kept = {**kept, "aliases": clean_aliases}

        index_mapping[old_idx] = len(filtered)
        filtered.append(kept)

    if removed_names or flagged_names:
        logger.info(
            "excluded_entities_filtered",
            removed_count=len(removed_names),
            removed_names=removed_names,
            flagged_count=len(flagged_names),
            flagged_names=flagged_names,
        )

    if filtering_log is not None:
        filtering_log.add_stage(
            "entity_exclusion_filter",
            input_count=len(entities),
            removed_count=len(removed_names),
            items=items,
            counts={EXCLUDED_NAME_STAGE_COUNT: len(flagged_names)},
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
    2. Symmetric: every symmetric relationship, in every chunk, is rewritten
       with its endpoints in canonical order; then the same chunk-aware collapse.
    3. Inverse: every relationship of an inverse pair, in every chunk, is
       rewritten to the pair's canonical label and direction; then the same
       chunk-aware collapse.

    Phases 2 and 3 orient across chunks (``RelationshipOrientation``), so the
    same fact written both ways in two groups leaves with one (source, target,
    type) and the commit stores it as one edge with a citation per group.

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


_ENDPOINT_KEY_PAIRS: tuple[tuple[str, str], ...] = (
    ("source", "target"),
    ("source_index", "target_index"),
    ("from", "to"),
)
"""Endpoint key pairs a relationship dict may carry; orientation swaps every pair present."""


def _default_endpoint_order(value: Any) -> tuple[int, Any]:
    """Sort key for an endpoint: integers numerically, anything else as text."""
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, value)
    return (1, "" if value is None else str(value))


@dataclass(frozen=True)
class RelationshipOrientation:
    """The one rule for the canonical form of a relationship fact.

    A fact has one stored form. A symmetric label (``interacts_with``,
    ``sibling_of``) says the same thing in both directions, so its endpoints
    are put in a fixed order. An inverse pair (``parent_of`` / ``child_of``)
    says the same thing with the endpoints swapped, so the fact takes the
    alphabetically-first label of the pair and the endpoints follow it. The
    relationship deduplication and the commit both orient with this, so a
    fact the LLM wrote both ways in two groups lands on one edge.

    Build it with :meth:`from_domain`; labels are compared lowercased.
    """

    symmetric: frozenset[str] = frozenset()
    inverse: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_domain(
        cls,
        symmetric_types: Iterable[str] | None = None,
        inverse_map: Mapping[str, str] | None = None,
    ) -> RelationshipOrientation:
        """Build the rule from a domain's symmetric labels and inverse map.

        Args:
            symmetric_types: Labels whose direction carries no meaning.
            inverse_map: Label -> its inverse label (either direction given).
                A label mapped to itself is symmetric.

        Returns:
            The orientation rule.

        """
        symmetric = {t.strip().lower() for t in symmetric_types or () if t}
        inverse: dict[str, str] = {}
        for a, b in (inverse_map or {}).items():
            if not a or not b:
                continue
            la, lb = a.strip().lower(), b.strip().lower()
            if la == lb:
                symmetric.add(la)
                continue
            inverse[la] = lb
            inverse[lb] = la
        return cls(symmetric=frozenset(symmetric), inverse=inverse)

    def __bool__(self) -> bool:
        """True when the rule can change any relationship."""
        return bool(self.symmetric or self.inverse)

    def orient(
        self,
        rel: dict[str, Any],
        endpoint_key: Callable[[Any], Any] | None = None,
    ) -> dict[str, Any]:
        """Return ``rel`` in its canonical form: the same dict when already canonical, else a copy.

        Args:
            rel: Relationship dict (``source``/``target``, ``source_index``/
                ``target_index`` or ``from``/``to`` endpoints, and ``type``).
            endpoint_key: Sort key for a symmetric fact's endpoints; the
                commit passes the endpoint's node id. Defaults to the
                endpoint value (integers numerically).

        Returns:
            The canonical relationship.

        """
        rtype = (rel.get("type") or "").strip().lower()
        other = self.inverse.get(rtype)
        if other is not None:
            return rel if rtype <= other else _swap_endpoints(rel, new_type=other)
        if rtype in self.symmetric:
            src, tgt = _relationship_endpoints(rel)
            key = endpoint_key or _default_endpoint_order
            if key(src) > key(tgt):
                return _swap_endpoints(rel)
        return rel


def _relationship_endpoints(rel: dict[str, Any]) -> tuple[Any, Any]:
    """The first endpoint pair ``rel`` carries, or ``(None, None)``."""
    for src_key, tgt_key in _ENDPOINT_KEY_PAIRS:
        if src_key in rel or tgt_key in rel:
            return rel.get(src_key), rel.get(tgt_key)
    return None, None


def _swap_endpoints(rel: dict[str, Any], new_type: str | None = None) -> dict[str, Any]:
    """A copy of ``rel`` with every endpoint pair swapped (and ``type`` set when given)."""
    swapped = dict(rel)
    for src_key, tgt_key in _ENDPOINT_KEY_PAIRS:
        if src_key in rel or tgt_key in rel:
            swapped[src_key], swapped[tgt_key] = rel.get(tgt_key), rel.get(src_key)
    if new_type is not None:
        swapped["type"] = new_type
    return swapped


def orient_relationships(
    relationships: list[dict[str, Any]],
    orientation: RelationshipOrientation,
    endpoint_key: Callable[[Any], Any] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Put every relationship in its canonical form (see :class:`RelationshipOrientation`).

    Args:
        relationships: Relationship dicts; not modified.
        orientation: The orientation rule.
        endpoint_key: Sort key for symmetric endpoints (see
            :meth:`RelationshipOrientation.orient`).

    Returns:
        Tuple of (oriented list in input order, number of relationships rewritten).

    """
    if not orientation:
        return list(relationships), 0
    oriented: list[dict[str, Any]] = []
    rewritten = 0
    for rel in relationships:
        canonical = orientation.orient(rel, endpoint_key)
        if canonical is not rel:
            rewritten += 1
        oriented.append(canonical)
    return oriented, rewritten


def _collapse_oriented(
    relationships: list[dict[str, Any]],
    orientation: RelationshipOrientation,
) -> tuple[list[dict[str, Any]], int]:
    """Orient every relationship, then collapse same-chunk duplicates of one fact.

    Orientation is applied across all chunks, so the same fact written both
    ways in two groups leaves here with one (source, target, type) and the
    commit stores one edge carrying a citation per group. Within one chunk,
    duplicates of the fact keep the highest-confidence row.

    Args:
        relationships: Pre-deduplicated relationship list.
        orientation: The orientation rule (only its symmetric or inverse part
            may be set, to report the phases separately).

    Returns:
        Tuple of (oriented, collapsed list; number removed).

    """
    oriented, _ = orient_relationships(relationships, orientation)
    final: dict[tuple[Any, Any, str, Any], dict[str, Any]] = {}
    for rel in oriented:
        src, tgt = _relationship_endpoints(rel)
        key = (src, tgt, (rel.get("type") or "").strip().lower(), rel.get("chunk_index"))
        existing = final.get(key)
        if existing is None or rel.get("confidence", 0.0) > existing.get("confidence", 0.0):
            final[key] = rel
    return list(final.values()), len(relationships) - len(final)


def _collapse_symmetric_pairs(
    relationships: list[dict[str, Any]],
    symmetric_types: frozenset[str],
) -> tuple[list[dict[str, Any]], int]:
    """Orient symmetric relationships and collapse same-chunk bidirectional duplicates.

    For symmetric types (e.g. interacts_with, spouse_of), (A, B) and (B, A)
    are one fact: every such relationship is rewritten with its endpoints in
    canonical order, in every chunk; within one chunk the duplicates collapse
    to the highest-confidence one. Entries from different chunks are
    preserved (one citation each) but now share their direction.

    Args:
        relationships: Pre-deduplicated relationship list.
        symmetric_types: Frozenset of symmetric type names.

    Returns:
        Tuple of (collapsed list, number removed).

    """
    result, sym_removed = _collapse_oriented(
        relationships, RelationshipOrientation.from_domain(symmetric_types=symmetric_types)
    )
    if sym_removed > 0:
        logger.info(
            "symmetric_relationships_collapsed",
            before=len(relationships),
            after=len(result),
            removed=sym_removed,
        )
    return result, sym_removed


def _collapse_inverse_pairs(
    relationships: list[dict[str, Any]],
    inverse_map: dict[str, str],
) -> tuple[list[dict[str, Any]], int]:
    """Rewrite inverse pairs into their canonical direction and collapse same-chunk duplicates.

    ``(A, parent_of, B)`` and ``(B, child_of, A)`` express the same fact:
    every such relationship, in every chunk, is rewritten to the
    alphabetically-first type of its pair with the endpoints following it;
    within one chunk the duplicates collapse to the highest-confidence one.

    Args:
        relationships: Pre-deduplicated relationship list.
        inverse_map: Mapping of edge type to its inverse type.

    Returns:
        Tuple of (collapsed list, number removed).

    """
    if not inverse_map:
        return relationships, 0
    orientation = RelationshipOrientation.from_domain(inverse_map=inverse_map)
    # Self-inverse labels are symmetric; they are oriented by the symmetric phase.
    orientation = RelationshipOrientation(inverse=orientation.inverse)
    result, inv_removed = _collapse_oriented(relationships, orientation)
    if inv_removed > 0:
        logger.info(
            "inverse_pair_relationships_collapsed",
            before=len(relationships),
            after=len(result),
            removed=inv_removed,
        )
    return result, inv_removed


def _fuzzy_type_match(entity_type: str, allowed_types: list[str]) -> bool:
    """Whether an entity's type is one an edge template allows: compared as core types.

    Edge templates name core types (``source_types``/``target_types``) and
    every entity leaves the per-chunk filters core-typed, its subtype a
    property (spec 2026-10-07 § 6). Two names match when they are the same
    name (case-insensitive), or when the mapping table
    (``type_rescue.FLOOR_TYPE_VOCABULARY``) gives both the same core type: a
    record still typed ``Character`` is a Person. A name no table knows
    matches only itself. Until 2026-10-07 a substring or a shared word was
    enough (``Personality`` passed for ``Person``, ``Military Location`` for
    ``Location``).

    Args:
        entity_type: The entity's type.
        allowed_types: The template's allowed types.

    Returns:
        True if the entity's type is one of the allowed types.

    """
    from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
        FLOOR_TYPE_VOCABULARY,
    )

    if not entity_type or not allowed_types:
        return False
    type_lower = entity_type.strip().lower()
    if any(type_lower == allowed.strip().lower() for allowed in allowed_types):
        return True
    own = FLOOR_TYPE_VOCABULARY.resolve(entity_type)
    if own is None:
        return False
    return any(
        (core := FLOOR_TYPE_VOCABULARY.resolve(allowed)) is not None and core[0] == own[0]
        for allowed in allowed_types
    )


def _label_words(label: str) -> frozenset[str]:
    """The words of a canonical relationship label that say something (no function words)."""
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        FUNCTION_WORDS,
    )

    return frozenset(w for w in label.split("_") if len(w) > 1 and w not in FUNCTION_WORDS)


def _fuzzy_edge_type_lookup(
    rel_type: str,
    edge_type_constraints: Mapping[str, Any],
) -> str | None:
    """The one template a near-miss relationship label means, or None.

    Both sides in canonical form (``relation_vocabulary.canonical_edge_label``).
    A label matches a template when the words that say something (function
    words left out) of one are the other's (``spouse`` is ``spouse_of``,
    ``is_parent_of`` is ``parent_of``), and only one template matches. Until
    audit wave 5 a substring or one shared word was enough, so ``of``
    matched the first ``*_of`` template, ``with`` matched ``interacts_with``
    and ``engaged_to`` matched ``betrothed_to`` on "to".

    Args:
        rel_type: Canonical relationship label to look up.
        edge_type_constraints: Constraints keyed by canonical template label.

    Returns:
        The matched template key, or None (no match, or more than one).

    """
    if not rel_type or not edge_type_constraints:
        return None
    if rel_type in edge_type_constraints:
        return rel_type
    words = _label_words(rel_type)
    if not words:
        return None
    matches = [
        key
        for key in edge_type_constraints
        if (key_words := _label_words(key)) and (words <= key_words or key_words <= words)
    ]
    return matches[0] if len(matches) == 1 else None


UNMATCHED_LABEL_STAGE_COUNT = "unmatched_label"
"""The ``relationship_type_constraint`` stage's count of the edges kept as the fallback label."""

TYPE_MISMATCH_STAGE_COUNT = "type_mismatch"
"""The ``relationship_type_constraint`` stage's count of the edges kept with refused endpoint types."""

TYPE_CONSTRAINT_STAGE_COUNTS: tuple[str, ...] = (
    UNMATCHED_LABEL_STAGE_COUNT,
    TYPE_MISMATCH_STAGE_COUNT,
)
"""The strict type-constraint check's flag counts, one per edge flag it writes (2026-10-08).

Until then the check dropped these edges and the stage's ``removed_count``
counted them; it drops nothing now."""


def type_constraint_flags(stages: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """The relationships the type-constraint check kept flagged, by flag, over a filtering log's ``stages``.

    What the source row's ``relationships_unmatched_label_kept`` and
    ``relationships_type_mismatch_kept`` count, and, together,
    ``relationships_dropped_type_unmatched`` (the extraction finalizer reads it),
    since 2026-10-08: the ``relationship_type_constraint`` stages'
    :data:`TYPE_CONSTRAINT_STAGE_COUNTS`. Until then the stage's
    ``removed_count``, the edges the check dropped.
    """
    totals = dict.fromkeys(TYPE_CONSTRAINT_STAGE_COUNTS, 0)
    for stage in stages:
        if stage.get("stage") != "relationship_type_constraint":
            continue
        for name in TYPE_CONSTRAINT_STAGE_COUNTS:
            totals[name] += int((stage.get("counts") or {}).get(name, 0) or 0)
    return totals


def flag_unmatched_label(rel: Mapping[str, Any], fallback: str) -> dict[str, Any]:
    """``rel`` kept although no template answers to its label: a copy as ``fallback``, flagged.

    The one writer of ``name_identity.UNMATCHED_LABEL_KEY``: the label becomes
    the fallback (``core_types.FALLBACK_LABEL``, ``related_to``) and the one the
    model wrote is kept beside the flag (``WRITTEN_LABEL_KEY``).
    """
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        UNMATCHED_LABEL_KEY,
        WRITTEN_LABEL_KEY,
        with_pipeline_flag,
    )

    stored = rel.get("properties")
    props = with_pipeline_flag(
        stored if isinstance(stored, Mapping) else None,
        UNMATCHED_LABEL_KEY,
        on=True,
        **{WRITTEN_LABEL_KEY: rel.get("type")},
    )
    return {**rel, "type": fallback, "properties": props}


def flag_type_mismatch(rel: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """``rel`` kept although its template refuses its endpoints' types: a copy flagged, label unchanged.

    The one writer of ``name_identity.TYPE_MISMATCH_KEY``, the reason beside
    it (``TYPE_MISMATCH_REASON_KEY``).
    """
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        TYPE_MISMATCH_KEY,
        TYPE_MISMATCH_REASON_KEY,
        with_pipeline_flag,
    )

    stored = rel.get("properties")
    props = with_pipeline_flag(
        stored if isinstance(stored, Mapping) else None,
        TYPE_MISMATCH_KEY,
        on=True,
        **{TYPE_MISMATCH_REASON_KEY: reason},
    )
    return {**rel, "properties": props}


def _type_mismatch_reason(
    label: str, source_type: str, target_type: str, *, source_ok: bool, allowed: Mapping[str, Any]
) -> str:
    """Why ``label``'s template refuses a relationship's endpoints (one sentence)."""
    if not source_ok:
        end, written, types = "source", source_type, allowed.get("source_types", [])
    else:
        end, written, types = "target", target_type, allowed.get("target_types", [])
    return f"{label} takes a {end} of type {', '.join(types)}, not {written or 'an untyped entity'}"


def validate_relationship_type_constraints(  # noqa: PLR0912, PLR0915
    relationships: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    edge_type_constraints: dict[str, dict[str, list[str]]],
    filtering_log: FilteringLog | None = None,
    *,
    strict_edge_type_constraints: bool = False,
    enable_direction_correction: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Check relationships against edge template type constraints: keep, flag, never delete.

    For each relationship, checks that source and target entity types are
    allowed by the edge template's ``source_types``/``target_types``
    constraints, compared as core types (:func:`_fuzzy_type_match`); a
    near-miss *label* is matched to its one template
    (:func:`_fuzzy_edge_type_lookup`). Under strict constraints (every
    extraction plan) the check keeps every relationship (2026-10-08; until
    then it dropped these, justification and all):

    - a label no template answers to, even by the near-miss match, is kept
      as the fallback label (``core_types.FALLBACK_LABEL``), flagged
      ``unmatched_label`` with the label as written
      (:func:`flag_unmatched_label`);
    - endpoints of types the template refuses either way round (or that fit
      only reversed while direction correction is off) are kept, label and
      direction unchanged, flagged ``type_mismatch`` with the reason
      (:func:`flag_type_mismatch`).

    Args:
        relationships: Validated relationships (source/target are int indices).
        entities: Entity list for type lookup.
        edge_type_constraints: Mapping of edge_type -> {"source_types": [...], "target_types": [...]}.
        filtering_log: Optional log collector for pipeline diagnostics.
        strict_edge_type_constraints: When True, flag relationships the
            constraints refuse (above). When False, unmatched labels and
            refused types pass through unchecked and unflagged (``fell_through``).
        enable_direction_correction: When True (default), swap source/target when
            the swap would satisfy type constraints. When False, keep the
            relationship as written, flagged ``type_mismatch`` (in either
            mode: until 2026-10-08 it was dropped). The counter increments
            either way so the
            wrong-direction LLM emission rate is always visible.

    Returns:
        Tuple of (relationships, stats) where stats has keys
        ``total_checked``, ``unmatched_label``, ``type_mismatch``,
        ``fuzzy_matched``, ``fell_through``, ``direction_corrected``.

    """
    if not relationships or not edge_type_constraints:
        return relationships, {
            "total_checked": len(relationships),
            UNMATCHED_LABEL_STAGE_COUNT: 0,
            TYPE_MISMATCH_STAGE_COUNT: 0,
            "fuzzy_matched": 0,
            "fell_through": 0,
        }

    from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
        FALLBACK_LABEL,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        canonical_edge_label,
    )

    filtered: list[dict[str, Any]] = []
    unmatched_label = 0
    type_mismatch = 0
    fuzzy_matched = 0
    fell_through = 0
    direction_corrected = 0
    flagged_items: list[FilteredItem] = []

    # One label normaliser for the filter and the commit: a template is matched
    # in the form the commit persists the label in.
    by_label: dict[str, tuple[str, dict[str, list[str]]]] = {
        canonical_edge_label(name): (name, entry) for name, entry in edge_type_constraints.items()
    }
    fallback_label = by_label.get(canonical_edge_label(FALLBACK_LABEL), (FALLBACK_LABEL, {}))[0]
    for rel in relationships:
        rel_type = canonical_edge_label(rel.get("type"))
        exact = by_label.get(rel_type)
        constraints = exact[1] if exact else None
        if exact and rel.get("type") != exact[0]:
            rel["type"] = exact[0]

        # No exact match — try fuzzy matching, then fall-through
        if not constraints:
            matched_key = _fuzzy_edge_type_lookup(rel_type, by_label)
            if matched_key:
                template_name, constraints = by_label[matched_key]
                fuzzy_matched += 1
                logger.info(
                    "relationship_type_fuzzy_matched",
                    original_type=rel_type,
                    matched_type=template_name,
                )
                # The relationship takes the template's label: a near-miss is the
                # template it was matched to, never an invented label kept.
                rel["type"] = template_name
            elif strict_edge_type_constraints:
                # Strict: kept as the fallback label, flagged, the written label beside it.
                unmatched_label += 1
                _log_filtered(
                    filtering_log,
                    flagged_items,
                    item_type="relationship",
                    name=_resolve_rel_name(rel, entities),
                    entity_type=rel_type,
                    reason=(
                        f"Relationship type '{rel_type}' not in domain templates: "
                        f"kept as {fallback_label}, flagged"
                    ),
                    details={"rel_type": rel_type, "kept": True},
                )
                filtered.append(flag_unmatched_label(rel, fallback_label))
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
        # constraint failure so it surfaces as a strict-mode flag or the
        # fell_through counter, never invisibly.
        source_ok = not source_types or _fuzzy_type_match(source_type, source_types)
        target_ok = not target_types or _fuzzy_type_match(target_type, target_types)

        if not source_ok or not target_ok:
            # Try swapping direction — LLM often gets source/target backwards
            swapped_source_ok = not source_types or _fuzzy_type_match(target_type, source_types)
            swapped_target_ok = not target_types or _fuzzy_type_match(source_type, target_types)
            swap_fits = swapped_source_ok and swapped_target_ok

            if swap_fits:
                # The swap would fix the direction — always increment the
                # counter (measures wrong-direction LLM emission rate,
                # independent of the toggle).
                direction_corrected += 1
            if swap_fits and enable_direction_correction:
                # Default (toggle on): swap and keep the relationship.
                rel["source"], rel["target"] = rel["target"], rel["source"]
                logger.info(
                    "relationship_direction_corrected",
                    rel_type=rel_type,
                    original_source=source_type,
                    original_target=target_type,
                )
            elif strict_edge_type_constraints or swap_fits:
                # Kept as written, flagged with the reason: under strict constraints,
                # and a reversible one with the toggle off in any mode (until
                # 2026-10-08 both were dropped).
                type_mismatch += 1
                reason = (
                    f"the endpoints fit {rel['type']} only reversed, and direction "
                    "correction is off"
                    if swap_fits
                    else _type_mismatch_reason(
                        str(rel["type"]),
                        source_type,
                        target_type,
                        source_ok=source_ok,
                        allowed=constraints,
                    )
                )
                _log_filtered(
                    filtering_log,
                    flagged_items,
                    item_type="relationship",
                    name=_resolve_rel_name(rel, entities),
                    entity_type=rel_type,
                    reason=f"{reason}: kept flagged",
                    details={
                        "source_type": source_type,
                        "target_type": target_type,
                        "allowed_source": source_types,
                        "allowed_target": target_types,
                        "kept": True,
                    },
                )
                filtered.append(flag_type_mismatch(rel, reason))
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
        UNMATCHED_LABEL_STAGE_COUNT: unmatched_label,
        TYPE_MISMATCH_STAGE_COUNT: type_mismatch,
        "fuzzy_matched": fuzzy_matched,
        "fell_through": fell_through,
        "direction_corrected": direction_corrected,
    }

    if unmatched_label or type_mismatch:
        logger.info(
            "relationship_type_constraints_flagged",
            **stats,
            total_after=len(filtered),
        )

    if filtering_log is not None:
        filtering_log.add_stage(
            "relationship_type_constraint",
            input_count=len(relationships),
            removed_count=0,
            items=flagged_items,
            counts={
                UNMATCHED_LABEL_STAGE_COUNT: unmatched_label,
                TYPE_MISMATCH_STAGE_COUNT: type_mismatch,
            },
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


def move_relation_descriptors(
    entities: list[dict[str, Any]],
    conventions: NameConventions,
    filtering_log: FilteringLog | None = None,
) -> int:
    """Move aliases naming another person's relation into ``descriptors``: ``Natásha's mother``.

    Such an alias says who the entity is to someone else, never who it is,
    and read as a name it makes the entity that someone (the name rule's
    descriptor: ``name_identity.descriptor_possessor``, under the domain's
    relation, role and generation words). It is kept as a descriptor (the
    commit stores ``descriptors``, the name rule reads one as evidence that
    the entity is *not* the person it names). ``run_deduplication`` runs
    this before the name rule, so neither the exact dedup's alias keys nor
    the hierarchical pass read one as a name.

    Modifies entities in-place.

    Args:
        entities: Entity list (modified in-place).
        conventions: The source domain's naming conventions.
        filtering_log: Optional log collector for pipeline diagnostics.

    Returns:
        Number of aliases moved.
    """
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        is_relation_alias,
    )

    moved: list[tuple[str, str]] = []
    for entity in entities:
        # A person's only: "Caesar's Commentaries" on a work is a name, not someone's relation
        etype = str(entity.get("type") or "")
        aliases = entity.get("aliases") or []
        out = [
            a for a in aliases if isinstance(a, str) and is_relation_alias(a, conventions, etype)
        ]
        if not out:
            continue
        name = str(entity.get("name") or entity.get("label") or "")
        entity["aliases"] = [a for a in aliases if a not in out]
        descriptors = list(entity.get("descriptors") or [])
        entity["descriptors"] = descriptors + [a for a in out if a not in descriptors]
        moved.extend((name, a) for a in out)
    for name, alias in moved:
        logger.info("relation_descriptor_alias_moved", alias=alias, entity_name=name)
    if filtering_log is not None and moved:
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteredItem,
        )

        filtering_log.add_stage(
            "relation_descriptor_aliases",
            input_count=sum(len(e.get("aliases") or []) for e in entities) + len(moved),
            removed_count=len(moved),
            items=[
                FilteredItem(
                    item_type="entity",
                    name=alias,
                    entity_type="(alias)",
                    reason="Alias names another person's relation: kept as a descriptor",
                    details={"entity": name},
                )
                for name, alias in moved
            ],
        )
    return len(moved)


def clean_descriptor_aliases(  # noqa: PLR0912 - one branch per kind of alias
    entities: list[dict[str, Any]],
    title_words: frozenset[str] | None = None,
    filtering_log: FilteringLog | None = None,
    conventions: NameConventions | None = None,
) -> int:
    """Move aliases that describe rather than name to ``descriptors``; remove none.

    An alias should be an alternate form of the entity's name (e.g.,
    "Annette" for "Anna Pávlovna Schérer"). An alias that shares no
    significant word root with the entity's names and that its text does not
    write (unattested: ``name_identity.record_attested_names``, the stamp
    ``name_identity.evidence_aliases`` reads) is likely a descriptor injected
    by the LLM (e.g., "Nieces" for "Empress Márya Fëdorovna"): it is moved to
    ``descriptors``, where the name rule reads no name from it. With
    ``conventions`` an alias naming another person's relation is a descriptor
    whatever words it shares, moved first (:func:`move_relation_descriptors`:
    ``Bezúkhov's son`` on Pierre; a merge may have brought one in).

    Keep, flag, never delete (2026-10-07): an attested alias stays an alias,
    whatever words it shares with the label ("Annette", "Lise", "Moskva": a
    given name or a spelling the text writes for this entity), and no alias is
    removed: until then every alias sharing no word was deleted, attested or
    not. A name a merge folded into the entity
    (``name_identity.MERGED_NAMES_KEY``) stays too: it was an entity's own
    name, and its words count as the entity's for the second pass (until
    2026-10-07 the v4 Pierre, folded into the one-mention ``Pétya``, lost
    ``Pierre``, ``Monsieur Pierre`` and ``Pierre Bezúkhov`` here).

    Modifies entities in-place.

    Args:
        entities: Entity list (modified in-place).
        title_words: Title/honorific words to ignore when comparing.
        filtering_log: Optional log collector for pipeline diagnostics
            (the ``descriptor_alias_cleaning`` stage: ``removed_count`` the
            aliases moved off ``aliases``, ``counts`` the moved and the
            attested ones kept).
        conventions: The source domain's naming conventions, for the
            relation descriptors; None skips that check.

    Returns:
        Total number of aliases moved to ``descriptors``.

    """
    relation_moved = (
        move_relation_descriptors(entities, conventions, filtering_log)
        if conventions is not None
        else 0
    )
    if not title_words:
        title_words = frozenset()

    # Also treat common articles as stop words for comparison
    stop_words = title_words | frozenset({"the", "a", "an", "of", "de", "von", "van", "le", "la"})

    total_moved = 0
    total_kept_attested = 0
    import unicodedata

    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        fold,
        record_attested_names,
        record_merged_names,
    )

    for entity in entities:
        aliases = entity.get("aliases", [])
        if not aliases:
            continue

        name = entity.get("name") or entity.get("label", "")
        name_words = _significant_words(name, stop_words)
        if not name_words:
            continue
        merged_in = {fold(n) for n in record_merged_names(entity)}
        attested = record_attested_names(entity)

        def _attested(alias: str, attested: frozenset[str] = attested) -> bool:
            return unicodedata.normalize("NFC", alias.strip()) in attested

        # Two-pass alias validation:
        # Pass 1: accept aliases that share a word root with the entity name
        # Pass 2: accept remaining aliases that share a root with accepted aliases
        # This handles cases like "Count Bezukhov" for entity "Pierre" —
        # it passes via "Pierre Bezukhov" which was accepted in pass 1.
        accepted: list[str] = []
        accepted_words: set[str] = set(name_words)
        deferred: list[tuple[str, set[str]]] = []
        unmatched: list[str] = []

        for alias in aliases:
            alias_words = _significant_words(alias, stop_words)

            if fold(alias) in merged_in or _attested(alias):
                accepted.append(alias)  # the entity's own name, or one its text writes: kept
                accepted_words.update(alias_words)
                continue
            if not alias_words:
                unmatched.append(alias)
                continue

            if _shares_word_root(alias_words, name_words):
                accepted.append(alias)
                accepted_words.update(alias_words)
            else:
                deferred.append((alias, alias_words))

        # Pass 2: check deferred aliases against accepted word pool
        for alias, alias_words in deferred:
            if _shares_word_root(alias_words, accepted_words):
                accepted.append(alias)
                accepted_words.update(alias_words)
            else:
                unmatched.append(alias)

        total_kept_attested += sum(
            1
            for a in accepted
            if _attested(a)
            and fold(a) not in merged_in
            and not _shares_word_root(_significant_words(a, stop_words), name_words)
        )
        if unmatched:
            for alias in unmatched:
                logger.info(
                    "descriptor_alias_moved",
                    alias=alias,
                    entity_name=name,
                    reason="unattested_no_name_overlap",
                )
            descriptors = list(entity.get("descriptors") or [])
            entity["descriptors"] = descriptors + [a for a in unmatched if a not in descriptors]
            total_moved += len(unmatched)
        if len(accepted) != len(aliases):
            entity["aliases"] = [a for a in aliases if a in accepted]

    if filtering_log is not None and (total_moved or total_kept_attested):
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
            FilteredItem,
        )

        # Summary item — individual aliases aren't distinct items
        total_aliases = sum(len(e.get("aliases", [])) for e in entities) + total_moved
        filtering_log.add_stage(
            "descriptor_alias_cleaning",
            input_count=total_aliases,
            removed_count=total_moved,
            items=[
                FilteredItem(
                    item_type="entity",
                    name=f"{total_moved} descriptor aliases",
                    entity_type="(various)",
                    reason=(
                        "Alias shares no word root with the entity's names and its text "
                        "does not write it: kept as a descriptor"
                    ),
                    details={"moved": total_moved, "kept_attested": total_kept_attested},
                )
            ],
            counts={"moved": total_moved, "kept_attested": total_kept_attested},
        )

    return total_moved + relation_moved


LOW_PLAUSIBILITY_STAGE_COUNT = "low_plausibility"
"""The ``implausible_entity_filter`` stage's count of the entities kept flagged below the bar."""


def filter_implausible_entities(
    entities: list[dict[str, Any]],
    source_sentences: list[str],
    named_referent_types: set[str] | None = None,
    threshold: float = 0.40,
    threshold_non_named: float = 0.15,
    filtering_log: FilteringLog | None = None,
    conventions: NameConventions | None = None,
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
    ``threshold_non_named``. A record written under a reference that says
    something of its own (:func:`kept_as_reference`: ``the count``, ``the
    army``) is never scored: its name is a reference by design, not a
    hallucination.

    Keep, flag, never delete (2026-10-07): an entity below the bar that
    carries evidence (:func:`carries_evidence`) is kept, flagged
    ``low_plausibility`` with its score (:func:`flag_low_plausibility`); only
    one carrying nothing (and an empty name) is dropped. Until then every
    entity below the bar was dropped, description and all (the v4 literary
    replay: ``Dining Room``, ``drawing room``, ``conservatory``, ``doll``).
    The stage's ``removed_count`` counts the drops and its ``counts`` the
    flags (``low_plausibility``).

    Args:
        entities: Entity list from extraction.
        source_sentences: The numbered sentences fed to the LLM.
        named_referent_types: Entity types that require proper names. If None,
            the filter applies uniformly to all types.
        threshold: Score below which named-type entities are rejected (default 0.40).
        threshold_non_named: Score below which non-named-type entities are
            rejected (default 0.15).
        filtering_log: Optional log collector for pipeline diagnostics.
        conventions: The domain's naming conventions (what a reference is).

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
    flagged: list[tuple[str, str, float]] = []
    flagged_items: list[FilteredItem] = []

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

        if kept_as_reference(entity, conventions):
            new_idx = len(filtered)
            filtered.append(entity)
            index_mapping[old_idx] = new_idx
            continue

        # Type-aware thresholding
        is_named_type = named_lower is None or entity_type.lower() in named_lower

        score = _name_plausibility_score(
            name, source_text_lower, source_text_original, is_named_type
        )

        effective_threshold = threshold if is_named_type else threshold_non_named

        if score < effective_threshold and carries_evidence(entity):
            flagged.append((name, entity_type, score))
            _log_filtered(
                filtering_log,
                flagged_items,
                item_type="entity",
                name=name,
                entity_type=entity_type,
                reason=(
                    f"Plausibility score {score:.2f} < {effective_threshold:.2f}: "
                    "kept flagged (carries evidence)"
                ),
                details={"score": round(score, 3), "threshold": effective_threshold, "kept": True},
            )
            index_mapping[old_idx] = len(filtered)
            filtered.append(flag_low_plausibility(entity, score))
        elif score < effective_threshold:
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

    if removed or flagged:
        logger.info(
            "implausible_entities_filtered",
            removed_count=len(removed),
            removed=[{"name": n, "type": t, "score": round(s, 2)} for n, t, s in removed],
            flagged_count=len(flagged),
            flagged=[{"name": n, "type": t, "score": round(s, 2)} for n, t, s in flagged],
        )

    if filtering_log is not None:
        filtering_log.add_stage(
            "implausible_entity_filter",
            input_count=len(entities),
            removed_count=len(removed),
            items=removed_items + flagged_items,
            counts={LOW_PLAUSIBILITY_STAGE_COUNT: len(flagged)},
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
