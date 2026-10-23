# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The dedup type gate, the partition and the name rule read core types (spec 2026-10-07 § 6).

Every entity leaves the per-chunk filters as a core type with its subtype in
``properties.entity_subtype`` (the type rescue). Two mentions of one name
typed by two subtypes of one core type are one entity, which keeps both
subtypes; two core types never merge under the type switch. Until
2026-10-07 the gate compared type strings, with a domain's
``compatibility_group`` map and a list of generic types (``Concept``,
``Object``) compatible with every type.
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.deduplication import name_identity as ni
from chaoscypher_core.services.sources.engine.deduplication.service import (
    EntityProcessor,
    merge_node_properties,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
    SUBTYPE_PROPERTY,
    SUBTYPES_PROPERTY,
)
from chaoscypher_core.settings import SourceProcessingSettings


def _entity(name: str, core: str, subtype: str | None = None, **extra: Any) -> dict[str, Any]:
    """An entity as it leaves the per-chunk filters."""
    props = {SUBTYPE_PROPERTY: subtype} if subtype else {}
    return {"name": name, "type": core, "aliases": [], "properties": props, **extra}


def test_two_subtypes_of_one_core_type_merge_and_keep_both_subtypes() -> None:
    """Person/character and Person/author under one name are one Person with both subtypes."""
    entities = [
        _entity("Leo Tolstoy", "Person", "character", source_chunk_indices=[0, 1]),
        _entity("Leo Tolstoy", "Person", "author", source_chunk_indices=[2]),
    ]
    unique, mapping = EntityProcessor().deduplicate_entities_with_mapping(
        entities, require_type_compatibility=True
    )

    assert len(unique) == 1
    assert mapping == {0: 0, 1: 0}
    (merged,) = unique
    assert merged["type"] == "Person"
    assert merged["properties"][SUBTYPES_PROPERTY] == ["author", "character"]
    assert merged["properties"][SUBTYPE_PROPERTY] in {"author", "character"}


def test_a_merge_keeps_the_survivors_subtype_and_the_core_type() -> None:
    """``entity_subtype`` is the kept record's, however confident the duplicate."""
    kept = _entity("Leo Tolstoy", "Person", "character", confidence=0.5)
    duplicate = _entity("Leo Tolstoy", "Person", "author", confidence=0.9)

    merged = EntityProcessor().merge_entities(kept, duplicate)

    assert merged["type"] == "Person"
    assert merged["properties"][SUBTYPE_PROPERTY] == "character"
    assert merged["properties"][SUBTYPES_PROPERTY] == ["author", "character"]


def test_equal_subtypes_or_none_change_nothing() -> None:
    """One subtype on both sides, or on one side only, writes no ``entity_subtypes``."""
    processor = EntityProcessor()
    same = processor.merge_entities(
        _entity("Pierre", "Person", "character"), _entity("Pierre", "Person", "character")
    )
    one = processor.merge_entities(
        _entity("Pierre", "Person"), _entity("Pierre", "Person", "author")
    )
    none = processor.merge_entities(_entity("Pierre", "Person"), _entity("Pierre", "Person"))

    assert same["properties"] == {SUBTYPE_PROPERTY: "character"}
    assert one["properties"] == {SUBTYPE_PROPERTY: "author"}
    assert none["properties"] == {}


def test_an_old_snapshots_subtype_name_still_dedups_with_its_core_type() -> None:
    """``Character`` (an old template name) and ``Person`` are one core type: one entity."""
    entities = [
        {"name": "Pierre", "type": "Character", "aliases": []},
        {"name": "Pierre", "type": "Person", "aliases": []},
    ]
    unique, _ = EntityProcessor().deduplicate_entities_with_mapping(
        entities, require_type_compatibility=True
    )

    assert len(unique) == 1


@pytest.mark.asyncio
async def test_different_core_types_never_merge() -> None:
    """Person "Moscow" and Location "Moscow" stay apart in the exact, semantic and hierarchical passes."""
    entities = [_entity("Moscow", "Person", "character"), _entity("Moscow", "Location", "city")]
    processor = EntityProcessor()

    exact, _ = processor.deduplicate_entities_with_mapping(
        [dict(e) for e in entities], require_type_compatibility=True
    )
    semantic, _, _ = await processor.deduplicate_entities_semantic(
        [dict(e) for e in entities],
        None,
        0.5,
        require_type_compatibility=True,
        precomputed_embeddings=[[1.0, 0.0], [1.0, 0.0]],
    )
    hierarchical, _, _ = processor.resolve_hierarchical_names([dict(e) for e in entities], [])

    assert [e["type"] for e in exact] == ["Person", "Location"]
    assert [e["type"] for e in semantic] == ["Person", "Location"]
    assert [e["type"] for e in hierarchical] == ["Person", "Location"]


def test_concept_and_object_are_no_generic_types() -> None:
    """A core type is never a wildcard: Concept "Mercury" and Person "Mercury" stay apart."""
    entities = [
        _entity("Mercury", "Concept"),
        _entity("Mercury", "Person"),
        _entity("Mercury", "Object"),
    ]
    unique, _ = EntityProcessor().deduplicate_entities_with_mapping(
        entities, require_type_compatibility=True
    )

    assert len(unique) == 3


def test_the_semantic_partition_groups_by_core_type() -> None:
    """``Character`` and ``Person`` share one comparison group; ``Location`` has its own."""
    entities = [
        {"name": "a", "type": "Character"},
        {"name": "b", "type": "Location"},
        {"name": "c", "type": "Person"},
        {"name": "d", "type": "region"},
    ]

    assert EntityProcessor._build_type_groups(entities) == [[0, 2], [1, 3]]


def test_the_hierarchical_pass_joins_names_of_one_core_type() -> None:
    """An old ``Character`` mention and a ``Person`` one join on the name rule (one core type)."""
    entities = [
        {"name": "Prince Andrew Bolkónski", "type": "Person", "aliases": ["Andrew"]},
        {"name": "Andrew", "type": "Character", "aliases": []},
    ]
    merged, _, mapping = EntityProcessor().resolve_hierarchical_names(entities, [])

    assert len(merged) == 1
    assert mapping == {0: 0, 1: 0}


def test_the_resolver_merge_keeps_both_subtypes() -> None:
    """The resolver's node merge shares the one subtype policy (``merge_subtypes``)."""
    merged = merge_node_properties(
        {SUBTYPE_PROPERTY: "character", "title": "Count"}, {SUBTYPE_PROPERTY: "historical_figure"}
    )

    assert merged[SUBTYPE_PROPERTY] == "character"
    assert merged[SUBTYPES_PROPERTY] == ["character", "historical_figure"]


def test_the_name_rule_reads_the_core_type() -> None:
    """``is_person`` reads core type Person; the typed index compares core types."""
    conventions = ni.NameConventions.build(person_types=["Character"])

    assert frozenset({"person"}) == ni.UNIVERSAL_PERSON_TYPES
    assert conventions.is_person("Person")
    assert conventions.is_person("Character")
    assert conventions.is_person("Historical Figure")
    assert not conventions.is_person("Work")
    assert not conventions.is_person("Literary Work")
    records = [
        ni.NameRecord("Prince Andrew Bolkónski", "Person", ("Andrew",)),
        ni.NameRecord("Andrew", "Character"),
    ]
    assert ni.plan_name_pairs(records, conventions=conventions)
    apart = [
        ni.NameRecord("Prince Andrew Bolkónski", "Person", ("Andrew",)),
        ni.NameRecord("Andrew", "Work"),
    ]
    assert not ni.plan_name_pairs(apart, conventions=conventions)


def test_an_old_settings_file_with_the_type_compatibility_map_still_loads() -> None:
    """``dedup_type_compatibility_map`` is removed (the core type is the group) and tolerated."""
    assert "dedup_type_compatibility_map" not in SourceProcessingSettings.model_fields
    parsed = SourceProcessingSettings.model_validate(
        {"dedup_type_compatibility_map": {"Person": ["Character"]}}
    )

    assert "dedup_type_compatibility_map" not in parsed.model_dump()
