# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for type_normalizer pure functions."""

from chaoscypher_core.services.sources.engine.extraction.utils.type_normalizer import (
    apply_type_aliases,
    filter_structural_entities,
    is_structural_entity,
)


# ============================================================================
# is_structural_entity
# ============================================================================


class TestIsStructuralEntity:
    """Tests for structural entity detection."""

    def test_chapter_by_name(self) -> None:
        assert is_structural_entity({"name": "Chapter 1"}) is True

    def test_section_by_name(self) -> None:
        assert is_structural_entity({"name": "Section 3.2"}) is True

    def test_appendix_by_name(self) -> None:
        assert is_structural_entity({"name": "Appendix A"}) is True

    def test_part_by_name(self) -> None:
        assert is_structural_entity({"name": "Part II"}) is True

    def test_figure_by_name(self) -> None:
        assert is_structural_entity({"name": "Figure 5"}) is True

    def test_not_structural(self) -> None:
        assert is_structural_entity({"name": "Albert Einstein"}) is False

    def test_structural_by_type(self) -> None:
        assert is_structural_entity({"name": "Intro", "type": "Chapter"}) is True

    def test_custom_structural_types(self) -> None:
        custom = {"module", "lesson"}
        assert is_structural_entity({"name": "Intro", "type": "module"}, custom) is True
        assert is_structural_entity({"name": "Intro", "type": "person"}, custom) is False


# ============================================================================
# filter_structural_entities
# ============================================================================


class TestFilterStructuralEntities:
    """Tests for batch structural filtering with index remapping."""

    def test_filters_structural_entities(self) -> None:
        entities = [
            {"name": "Chapter 1", "type": "chapter"},
            {"name": "Einstein", "type": "person"},
            {"name": "Section 2", "type": "section"},
            {"name": "MIT", "type": "organization"},
        ]
        filtered, _, index_map = filter_structural_entities(entities)
        assert len(filtered) == 2
        assert filtered[0]["name"] == "Einstein"
        assert filtered[1]["name"] == "MIT"

    def test_remaps_relationship_indices(self) -> None:
        entities = [
            {"name": "Chapter 1", "type": "chapter"},  # idx 0, removed
            {"name": "Einstein", "type": "person"},  # idx 1, becomes 0
            {"name": "MIT", "type": "organization"},  # idx 2, becomes 1
        ]
        relationships = [
            {"source": 1, "target": 2, "type": "works_at"},
        ]
        filtered_ents, filtered_rels, index_map = filter_structural_entities(
            entities, relationships
        )
        assert len(filtered_rels) == 1
        assert filtered_rels[0]["source"] == 0  # Was 1, remapped
        assert filtered_rels[0]["target"] == 1  # Was 2, remapped

    def test_removes_relationships_with_deleted_entities(self) -> None:
        entities = [
            {"name": "Chapter 1", "type": "chapter"},  # removed
            {"name": "Einstein", "type": "person"},
        ]
        relationships = [
            {"source": 0, "target": 1, "type": "contains"},  # Source removed
        ]
        _, filtered_rels, _ = filter_structural_entities(entities, relationships)
        assert len(filtered_rels) == 0

    def test_returns_index_mapping(self) -> None:
        entities = [
            {"name": "Chapter 1", "type": "chapter"},  # removed
            {"name": "Einstein", "type": "person"},  # 0
            {"name": "Section 2", "type": "section"},  # removed
            {"name": "MIT", "type": "organization"},  # 1
        ]
        _, _, index_map = filter_structural_entities(entities)
        assert index_map[0] is None
        assert index_map[1] == 0
        assert index_map[2] is None
        assert index_map[3] == 1

    def test_no_structural_entities(self) -> None:
        entities = [
            {"name": "Einstein", "type": "person"},
            {"name": "MIT", "type": "organization"},
        ]
        filtered, _, _ = filter_structural_entities(entities)
        assert len(filtered) == 2

    def test_empty_input(self) -> None:
        filtered, filtered_rels, index_map = filter_structural_entities([], [])
        assert filtered == []
        assert filtered_rels == []
        assert index_map == {}


# ============================================================================
# apply_type_aliases
# ============================================================================


class TestApplyTypeAliases:
    """Tests for apply_type_aliases(): a pack's aliases and the mapping table to core types.

    A pack may alias a name it does not declare to one of its subtypes
    (literary's ``Historical Figure`` -> ``Character``) or to a core type
    (``Suspect`` -> ``Person``). ``apply_type_aliases`` rewrites the entity
    type to the core type and keeps the subtype the mapping table gives under
    a property (default ``entity_subtype``), never overwriting one the record
    carries (spec 2026-10-07 § 6).
    """

    def test_rewrites_aliased_type(self) -> None:
        """An aliased type takes the core type of the alias's target."""
        entities = [{"name": "Napoleon", "type": "Historical Figure"}]
        count = apply_type_aliases(entities, {"Historical Figure": "Character"})

        assert count == 1
        assert entities[0]["type"] == "Person"

    def test_keeps_the_targets_subtype(self) -> None:
        """The subtype is the alias target's, from the mapping table."""
        entities = [{"name": "Napoleon", "type": "Historical Figure"}]
        apply_type_aliases(entities, {"Historical Figure": "Character"})

        assert entities[0]["properties"]["entity_subtype"] == "character"

    def test_preserves_existing_properties(self) -> None:
        """Pre-existing properties are not clobbered when subtype is added."""
        entities = [
            {
                "name": "Napoleon",
                "type": "Historical Figure",
                "properties": {"birth_year": "1769"},
            }
        ]
        apply_type_aliases(entities, {"Historical Figure": "Character"})

        assert entities[0]["properties"]["birth_year"] == "1769"
        assert entities[0]["properties"]["entity_subtype"] == "character"

    def test_never_overwrites_a_carried_subtype(self) -> None:
        entities = [
            {"name": "Napoleon", "type": "Historical Figure", "properties": {"entity_subtype": "x"}}
        ]
        apply_type_aliases(entities, {"Historical Figure": "Character"})

        assert entities[0]["type"] == "Person"
        assert entities[0]["properties"]["entity_subtype"] == "x"

    def test_a_core_type_is_left_as_is(self) -> None:
        """An entity already typed as a core type is left exactly as-is."""
        entities = [{"name": "Anna", "type": "Person"}]
        count = apply_type_aliases(entities, {"Historical Figure": "Character"})

        assert count == 0
        assert entities[0] == {"name": "Anna", "type": "Person"}

    def test_the_mapping_table_applies_without_aliases(self) -> None:
        """An old template name is a core type and subtype with no alias map at all."""
        entities = [
            {"name": "Napoleon", "type": "Historical Figure"},
            {"name": "Sword", "type": "Item"},
            {"name": "Glorp", "type": "Quux"},
        ]
        count = apply_type_aliases(entities, {})

        assert count == 2
        assert entities[0] == {
            "name": "Napoleon",
            "type": "Person",
            "properties": {"entity_subtype": "historical_figure"},
        }
        # Item is the core type's synonym: no subtype. A name no table knows is left for the rescue.
        assert entities[1] == {"name": "Sword", "type": "Object"}
        assert entities[2] == {"name": "Glorp", "type": "Quux"}

    def test_the_packs_qualified_rows_win(self) -> None:
        entities = [{"name": "The Times", "type": "Publication"}]
        apply_type_aliases(entities, {}, packs=("news",))

        assert entities[0]["type"] == "Organization"
        assert entities[0]["properties"]["entity_subtype"] == "publication"

    def test_empty_entities_is_noop(self) -> None:
        """Empty entity list: nothing happens."""
        entities: list[dict[str, object]] = []
        count = apply_type_aliases(entities, {"Historical Figure": "Character"})

        assert count == 0
        assert entities == []

    def test_idempotent(self) -> None:
        """Running twice doesn't double-rewrite or clobber the subtype."""
        entities = [{"name": "Napoleon", "type": "Historical Figure"}]
        apply_type_aliases(entities, {"Historical Figure": "Character"})
        count = apply_type_aliases(entities, {"Historical Figure": "Character"})

        assert count == 0
        assert entities[0]["type"] == "Person"
        assert entities[0]["properties"]["entity_subtype"] == "character"

    def test_multiple_aliases_one_pass(self) -> None:
        """Multiple alias mappings apply correctly in a single call."""
        entities = [
            {"name": "Napoleon", "type": "Historical Figure"},
            {"name": "Anna", "type": "Character"},
            {"name": "John Doe", "type": "Suspect"},
        ]
        aliases = {"Historical Figure": "Character", "Suspect": "Person"}
        count = apply_type_aliases(entities, aliases)

        assert count == 3
        assert [e["type"] for e in entities] == ["Person", "Person", "Person"]
        assert entities[0]["properties"]["entity_subtype"] == "character"
        assert entities[1]["properties"]["entity_subtype"] == "character"
        # An alias to a core type with no table row for the written name: no subtype (no free text).
        assert "properties" not in entities[2]

    def test_custom_subtype_property_name(self) -> None:
        """Caller can choose which property key holds the subtype."""
        entities = [{"name": "Napoleon", "type": "Historical Figure"}]
        apply_type_aliases(
            entities,
            {"Historical Figure": "Character"},
            subtype_property="original_type",
        )

        assert entities[0]["properties"]["original_type"] == "character"
        assert "entity_subtype" not in entities[0]["properties"]
