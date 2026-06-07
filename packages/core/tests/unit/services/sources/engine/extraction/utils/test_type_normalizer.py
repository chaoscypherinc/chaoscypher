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
    """Tests for apply_type_aliases() — domain-driven type canonicalization.

    Some plugins declare two NodeTemplates that name effectively the same
    concept (e.g. literary's ``Historical Figure`` vs ``Character``). The
    LLM picks one or the other inconsistently, fragmenting the graph and
    leaking real-world-grounding signal into entity_type when it belongs in
    a property. ``apply_type_aliases`` rewrites the entity type to the
    canonical value while preserving the original under a property
    (default ``entity_subtype``) so the signal isn't lost.
    """

    def test_rewrites_aliased_type(self) -> None:
        """Entity whose type is in the alias map gets the canonical type."""
        entities = [{"name": "Napoleon", "type": "Historical Figure"}]
        count = apply_type_aliases(entities, {"Historical Figure": "Character"})

        assert count == 1
        assert entities[0]["type"] == "Character"

    def test_preserves_original_as_subtype(self) -> None:
        """The original type is preserved under ``entity_subtype`` on properties."""
        entities = [{"name": "Napoleon", "type": "Historical Figure"}]
        apply_type_aliases(entities, {"Historical Figure": "Character"})

        assert entities[0]["properties"]["entity_subtype"] == "Historical Figure"

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
        assert entities[0]["properties"]["entity_subtype"] == "Historical Figure"

    def test_untouched_when_type_not_in_aliases(self) -> None:
        """An entity whose type isn't in the alias map is left exactly as-is."""
        entities = [{"name": "Anna", "type": "Character"}]
        count = apply_type_aliases(entities, {"Historical Figure": "Character"})

        assert count == 0
        assert entities[0] == {"name": "Anna", "type": "Character"}

    def test_empty_aliases_is_noop(self) -> None:
        """Empty alias map: nothing is rewritten."""
        entities = [{"name": "Napoleon", "type": "Historical Figure"}]
        count = apply_type_aliases(entities, {})

        assert count == 0
        assert entities[0] == {"name": "Napoleon", "type": "Historical Figure"}

    def test_empty_entities_is_noop(self) -> None:
        """Empty entity list: nothing happens."""
        entities: list[dict[str, object]] = []
        count = apply_type_aliases(entities, {"Historical Figure": "Character"})

        assert count == 0
        assert entities == []

    def test_idempotent(self) -> None:
        """Running twice doesn't double-rewrite or clobber the preserved subtype.

        After the first pass the entity's type is no longer in the alias map,
        so the second pass is a no-op — but importantly, the preserved
        ``entity_subtype`` from pass 1 survives.
        """
        entities = [{"name": "Napoleon", "type": "Historical Figure"}]
        apply_type_aliases(entities, {"Historical Figure": "Character"})
        count = apply_type_aliases(entities, {"Historical Figure": "Character"})

        assert count == 0
        assert entities[0]["type"] == "Character"
        assert entities[0]["properties"]["entity_subtype"] == "Historical Figure"

    def test_multiple_aliases_one_pass(self) -> None:
        """Multiple alias mappings apply correctly in a single call."""
        entities = [
            {"name": "Napoleon", "type": "Historical Figure"},
            {"name": "Anna", "type": "Character"},
            {"name": "John Doe", "type": "Suspect"},
        ]
        aliases = {"Historical Figure": "Character", "Suspect": "Person"}
        count = apply_type_aliases(entities, aliases)

        assert count == 2
        assert entities[0]["type"] == "Character"
        assert entities[0]["properties"]["entity_subtype"] == "Historical Figure"
        assert entities[1]["type"] == "Character"
        assert "properties" not in entities[1] or "entity_subtype" not in entities[1].get(
            "properties", {}
        )
        assert entities[2]["type"] == "Person"
        assert entities[2]["properties"]["entity_subtype"] == "Suspect"

    def test_custom_subtype_property_name(self) -> None:
        """Caller can choose which property key holds the preserved subtype."""
        entities = [{"name": "Napoleon", "type": "Historical Figure"}]
        apply_type_aliases(
            entities,
            {"Historical Figure": "Character"},
            subtype_property="original_type",
        )

        assert entities[0]["properties"]["original_type"] == "Historical Figure"
        assert "entity_subtype" not in entities[0]["properties"]
