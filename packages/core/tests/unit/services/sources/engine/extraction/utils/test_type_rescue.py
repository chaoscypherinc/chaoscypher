# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the three-tier type rescue system."""

from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
    _apply_property,
    _is_junk_entity,
    _try_absorb_as_property,
    _try_remap_type,
    rescue_invalid_entity_types,
)


# ---------------------------------------------------------------------------
# Tier 1: Junk Filter
# ---------------------------------------------------------------------------


class TestIsJunkEntity:
    """Tests for _is_junk_entity() junk detection."""

    def test_empty_name_is_junk(self):
        """Empty string names are junk."""
        assert _is_junk_entity("", "SomeType") is True

    def test_name_equals_type_is_junk(self):
        """Entity where name matches type (case-insensitive) is junk."""
        assert _is_junk_entity("Event", "event") is True
        assert _is_junk_entity("CHARACTER", "Character") is True

    def test_single_common_word_is_junk(self):
        """Single common/stop words are junk."""
        assert _is_junk_entity("the", "Misc") is True
        assert _is_junk_entity("it", "Misc") is True
        assert _is_junk_entity("was", "Misc") is True
        assert _is_junk_entity("They", "Misc") is True

    def test_valid_name_is_not_junk(self):
        """Normal entity names pass junk filter."""
        assert _is_junk_entity("Prince Andrei", "Character") is False
        assert _is_junk_entity("Battle of Austerlitz", "Event") is False

    def test_short_non_common_word_is_not_junk(self):
        """Short words that aren't in common set pass."""
        assert _is_junk_entity("AI", "Concept") is False
        assert _is_junk_entity("USA", "Location") is False


# ---------------------------------------------------------------------------
# Tier 2: Property Absorption
# ---------------------------------------------------------------------------


class TestTryAbsorbAsProperty:
    """Tests for _try_absorb_as_property() property absorption."""

    def _make_entity(self, name, entity_type, chunk_index=0, properties=None):
        """Create a minimal entity dict."""
        e = {"name": name, "type": entity_type, "chunk_index": chunk_index}
        if properties:
            e["properties"] = properties
        return e

    def test_absorb_via_relationship(self):
        """Property entity absorbed into target connected by relationship."""
        entities = [
            self._make_entity("Pierre", "Character", chunk_index=0),
            self._make_entity("Brave", "Personality Trait", chunk_index=0),
        ]
        mapping = {
            "Personality Trait": {"target_type": "Character", "property": "personality_traits"},
        }
        rel_targets = {1: [0]}  # entity 1 → entity 0
        rel_sources = {}

        result = _try_absorb_as_property(
            1,
            entities[1],
            entities,
            "Personality Trait",
            mapping,
            rel_targets,
            rel_sources,
        )
        assert result is True
        assert entities[0]["properties"]["personality_traits"] == "Brave"

    def test_absorb_via_proximity(self):
        """Property entity absorbed into nearby target in same chunk."""
        entities = [
            self._make_entity("Natasha", "Character", chunk_index=2),
            self._make_entity("Cheerful", "Personality Trait", chunk_index=2),
        ]
        mapping = {
            "Personality Trait": {"target_type": "Character", "property": "personality_traits"},
        }
        result = _try_absorb_as_property(
            1,
            entities[1],
            entities,
            "Personality Trait",
            mapping,
            {},
            {},
        )
        assert result is True
        assert entities[0]["properties"]["personality_traits"] == "Cheerful"

    def test_absorb_appends_with_semicolon(self):
        """Multiple absorbed values are joined with semicolon."""
        entities = [
            self._make_entity(
                "Pierre", "Character", chunk_index=0, properties={"personality_traits": "Brave"}
            ),
            self._make_entity("Kind", "Personality Trait", chunk_index=0),
        ]
        mapping = {
            "Personality Trait": {"target_type": "Character", "property": "personality_traits"},
        }

        result = _try_absorb_as_property(
            1,
            entities[1],
            entities,
            "Personality Trait",
            mapping,
            {},
            {},
        )
        assert result is True
        assert entities[0]["properties"]["personality_traits"] == "Brave; Kind"

    def test_no_matching_type_returns_false(self):
        """Returns False when no entity has the required target type."""
        entities = [
            self._make_entity("Moscow", "Location", chunk_index=0),
            self._make_entity("Cold", "Personality Trait", chunk_index=0),
        ]
        mapping = {
            "Personality Trait": {"target_type": "Character", "property": "personality_traits"},
        }

        result = _try_absorb_as_property(
            1,
            entities[1],
            entities,
            "Personality Trait",
            mapping,
            {},
            {},
        )
        assert result is False

    def test_no_mapping_returns_false(self):
        """Returns False when entity type not in property_type_mapping."""
        entities = [
            self._make_entity("Pierre", "Character", chunk_index=0),
            self._make_entity("Unknown", "RandomType", chunk_index=0),
        ]

        result = _try_absorb_as_property(
            1,
            entities[1],
            entities,
            "RandomType",
            {},
            {},
            {},
        )
        assert result is False

    def test_different_chunk_not_absorbed_by_proximity(self):
        """Proximity absorption requires same chunk_index."""
        entities = [
            self._make_entity("Pierre", "Character", chunk_index=0),
            self._make_entity("Brave", "Personality Trait", chunk_index=5),
        ]
        mapping = {
            "Personality Trait": {"target_type": "Character", "property": "personality_traits"},
        }

        result = _try_absorb_as_property(
            1,
            entities[1],
            entities,
            "Personality Trait",
            mapping,
            {},
            {},
        )
        assert result is False


# ---------------------------------------------------------------------------
# Tier 3: Type Remapping
# ---------------------------------------------------------------------------


class TestTryRemapType:
    """Tests for _try_remap_type() type remapping."""

    def test_remap_via_description_keyword(self):
        """Entity remapped when description matches a normalization keyword."""
        entity = {"name": "Battle", "type": "Event", "description": "A military conflict"}
        rules = {"Plot Element": ["battle", "conflict", "war"]}
        valid = {"Plot Element"}

        result = _try_remap_type(entity, "Event", rules, valid)
        assert result == "Plot Element"

    def test_remap_via_type_name_as_keyword(self):
        """Entity remapped when invalid type name matches a keyword exactly."""
        entity = {"name": "Austerlitz", "type": "Battle", "description": ""}
        rules = {"Event": ["battle", "ceremony", "incident"]}
        valid = {"Event"}

        result = _try_remap_type(entity, "Battle", rules, valid)
        assert result == "Event"

    def test_remap_via_substring_match(self):
        """Entity remapped when type name is substring of a keyword."""
        entity = {"name": "Rostov Family", "type": "Noble Family", "description": ""}
        rules = {"Social Group": ["family", "military unit", "class"]}
        valid = {"Social Group"}

        result = _try_remap_type(entity, "Noble Family", rules, valid)
        assert result == "Social Group"

    def test_no_match_returns_none(self):
        """Returns None when no normalization rule matches."""
        entity = {"name": "Something", "type": "Xyzzy", "description": "nothing relevant"}
        rules = {"Character": ["person", "individual"]}
        valid = {"Character"}

        result = _try_remap_type(entity, "Xyzzy", rules, valid)
        assert result is None

    def test_skips_rules_for_invalid_target(self):
        """Rules mapping to types not in valid_types are skipped."""
        entity = {"name": "Battle", "type": "Conflict", "description": "A battle"}
        rules = {"NotValid": ["battle", "conflict"]}
        valid = {"Character"}  # NotValid not in valid set

        result = _try_remap_type(entity, "Conflict", rules, valid)
        assert result is None


# ---------------------------------------------------------------------------
# Apply Property Helper
# ---------------------------------------------------------------------------


class TestApplyProperty:
    """Tests for _apply_property() helper."""

    def test_creates_properties_dict_if_missing(self):
        """Creates properties dict when entity has none."""
        entity = {"name": "Pierre", "type": "Character"}
        _apply_property(entity, "occupation", "Count")
        assert entity["properties"] == {"occupation": "Count"}

    def test_appends_with_semicolon(self):
        """Appends to existing property value with semicolon separator."""
        entity = {"name": "Pierre", "type": "Character", "properties": {"occupation": "Count"}}
        _apply_property(entity, "occupation", "Soldier")
        assert entity["properties"]["occupation"] == "Count; Soldier"


# ---------------------------------------------------------------------------
# Full Pipeline: rescue_invalid_entity_types()
# ---------------------------------------------------------------------------


class TestRescueInvalidEntityTypes:
    """Integration tests for the full rescue pipeline."""

    def _make_entity(self, name, entity_type, description="", chunk_index=0):
        """Create a minimal entity dict."""
        return {
            "name": name,
            "type": entity_type,
            "description": description,
            "chunk_index": chunk_index,
        }

    def test_all_valid_entities_pass_through(self):
        """When all entities have valid types, nothing changes."""
        entities = [
            self._make_entity("Pierre", "Character"),
            self._make_entity("Moscow", "Setting"),
        ]
        rels = [{"source": 0, "target": 1, "type": "located_in"}]
        valid = {"Character", "Setting"}

        rescued, rescued_rels, mapping, stats = rescue_invalid_entity_types(
            entities,
            rels,
            valid,
            {},
            {},
        )
        assert len(rescued) == 2
        assert len(rescued_rels) == 1
        assert mapping == {0: 0, 1: 1}
        assert stats["total_invalid"] == 0

    def test_empty_entities_pass_through(self):
        """Empty entity list returns immediately."""
        rescued, rescued_rels, _mapping, stats = rescue_invalid_entity_types(
            [],
            [],
            {"Character"},
            {},
            {},
        )
        assert rescued == []
        assert rescued_rels == []
        assert stats["total_invalid"] == 0

    def test_empty_valid_types_pass_through(self):
        """Empty valid_types set returns all entities unchanged."""
        entities = [self._make_entity("Pierre", "Whatever")]
        rescued, _rescued_rels, _mapping, stats = rescue_invalid_entity_types(
            entities,
            [],
            set(),
            {},
            {},
        )
        assert len(rescued) == 1
        assert stats["total_invalid"] == 0

    def test_tier1_drops_junk(self):
        """Tier 1 drops entities with empty names or name==type."""
        entities = [
            self._make_entity("Pierre", "Character"),
            self._make_entity("", "InvalidType"),  # empty name → junk
            self._make_entity("Event", "Event"),  # name == type → junk (invalid type)
        ]
        valid = {"Character"}

        rescued, _, mapping, stats = rescue_invalid_entity_types(
            entities,
            [],
            valid,
            {},
            {},
        )
        assert len(rescued) == 1
        assert rescued[0]["name"] == "Pierre"
        assert mapping[1] is None
        assert mapping[2] is None
        assert stats["tier1_junk"] == 2

    def test_tier2_absorbs_property(self):
        """Tier 2 absorbs property-like entity into target."""
        entities = [
            self._make_entity("Pierre", "Character", chunk_index=0),
            self._make_entity("Brave", "Personality Trait", chunk_index=0),
        ]
        rels = [{"source": 1, "target": 0, "type": "has_trait"}]
        valid = {"Character"}
        prop_map = {
            "Personality Trait": {"target_type": "Character", "property": "personality_traits"},
        }

        rescued, _rescued_rels, mapping, stats = rescue_invalid_entity_types(
            entities,
            rels,
            valid,
            {},
            prop_map,
        )
        assert len(rescued) == 1
        assert rescued[0]["name"] == "Pierre"
        assert rescued[0]["properties"]["personality_traits"] == "Brave"
        assert mapping[1] is None
        assert stats["tier2_absorbed"] == 1

    def test_tier3_remaps_type(self):
        """Tier 3 remaps invalid type to valid type, preserving relationships."""
        entities = [
            self._make_entity("Pierre", "Character"),
            self._make_entity(
                "Battle of Austerlitz", "Battle", description="A major military conflict"
            ),
        ]
        rels = [{"source": 0, "target": 1, "type": "participates_in"}]
        valid = {"Character", "Event"}
        norm_rules = {"Event": ["battle", "conflict", "war"]}

        rescued, rescued_rels, mapping, stats = rescue_invalid_entity_types(
            entities,
            rels,
            valid,
            norm_rules,
            {},
        )
        assert len(rescued) == 2
        assert rescued[1]["type"] == "Event"
        assert rescued[1]["type_rescued_from"] == "Battle"
        assert mapping[0] == 0
        assert mapping[1] == 1
        assert stats["tier3_remapped"] == 1
        # Relationship preserved
        assert len(rescued_rels) == 1

    def test_unrescued_dropped(self):
        """Entities that fail all tiers are dropped."""
        entities = [
            self._make_entity("Pierre", "Character"),
            self._make_entity(
                "Weird Entity", "CompletelyUnknownType", description="nothing useful"
            ),
        ]
        rels = [{"source": 0, "target": 1, "type": "related_to"}]
        valid = {"Character"}

        rescued, rescued_rels, mapping, stats = rescue_invalid_entity_types(
            entities,
            rels,
            valid,
            {},
            {},
        )
        assert len(rescued) == 1
        assert mapping[1] is None
        assert stats["unrescued_dropped"] == 1
        # Relationship dropped because target entity was removed
        assert len(rescued_rels) == 0

    def test_index_mapping_correctness(self):
        """Index mapping correctly remaps when middle entities are removed."""
        entities = [
            self._make_entity("Pierre", "Character"),  # 0 → 0 (valid)
            self._make_entity("", "Junk"),  # 1 → None (junk)
            self._make_entity("Natasha", "Character"),  # 2 → 1 (valid)
            self._make_entity("the", "Misc"),  # 3 → None (junk)
            self._make_entity("Moscow", "Setting"),  # 4 → 2 (valid)
        ]
        rels = [
            {"source": 0, "target": 2, "type": "knows"},
            {"source": 2, "target": 4, "type": "lives_in"},
            {"source": 0, "target": 1, "type": "bad_rel"},  # drops
        ]
        valid = {"Character", "Setting"}

        rescued, rescued_rels, mapping, _stats = rescue_invalid_entity_types(
            entities,
            rels,
            valid,
            {},
            {},
        )
        assert len(rescued) == 3
        assert mapping == {0: 0, 1: None, 2: 1, 3: None, 4: 2}
        # Two valid relationships survive with remapped indices
        assert len(rescued_rels) == 2
        assert rescued_rels[0]["source"] == 0
        assert rescued_rels[0]["target"] == 1  # was 2 → now 1
        assert rescued_rels[1]["source"] == 1  # was 2 → now 1
        assert rescued_rels[1]["target"] == 2  # was 4 → now 2

    def test_rescue_stats_accurate(self):
        """Rescue stats accurately reflect tier outcomes."""
        entities = [
            self._make_entity("Pierre", "Character"),  # valid
            self._make_entity("", "BadType"),  # tier 1 junk
            self._make_entity("Brave", "Personality Trait", chunk_index=0),  # tier 2 absorb
            self._make_entity("Big Battle", "Battle", description="A conflict"),  # tier 3 remap
            self._make_entity("Xyz", "CompletelyUnknown"),  # unrescued
        ]
        # Relationship: entity 2 → entity 0 (for tier 2 absorption)
        rels = [{"source": 2, "target": 0, "type": "has_trait"}]
        valid = {"Character", "Event"}
        norm_rules = {"Event": ["battle", "conflict"]}
        prop_map = {
            "Personality Trait": {"target_type": "Character", "property": "personality_traits"},
        }

        _, _, _, stats = rescue_invalid_entity_types(
            entities,
            rels,
            valid,
            norm_rules,
            prop_map,
        )
        assert stats["total_invalid"] == 4
        assert stats["tier1_junk"] == 1
        assert stats["tier2_absorbed"] == 1
        assert stats["tier3_remapped"] == 1
        assert stats["unrescued_dropped"] == 1

    def test_tier3_preserves_all_relationships(self):
        """Tier 3 remapped entities keep all their relationships intact."""
        entities = [
            self._make_entity("Pierre", "Character"),
            self._make_entity("Natasha", "Character"),
            self._make_entity(
                "Battle of Borodino", "Military Event", description="A massive battle"
            ),
        ]
        rels = [
            {"source": 0, "target": 2, "type": "participates_in"},
            {"source": 1, "target": 2, "type": "participates_in"},
            {"source": 0, "target": 1, "type": "knows"},
        ]
        valid = {"Character", "Event"}
        norm_rules = {"Event": ["battle", "military"]}

        rescued, rescued_rels, _mapping, stats = rescue_invalid_entity_types(
            entities,
            rels,
            valid,
            norm_rules,
            {},
        )
        assert len(rescued) == 3
        assert rescued[2]["type"] == "Event"
        # All 3 relationships preserved
        assert len(rescued_rels) == 3
        assert stats["tier3_remapped"] == 1
