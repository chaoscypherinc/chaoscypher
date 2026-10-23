# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for TemplateExtractor static methods."""

from chaoscypher_core.services.sources.engine.extraction.utils.template_extractor import (
    TemplateExtractor,
)


# ============================================================================
# generate_suggestions_from_entities
# ============================================================================


class TestGenerateSuggestionsFromEntities:
    """Tests for entity type suggestion generation."""

    def test_generates_suggestions(self) -> None:
        entities = [
            {"type": "Person", "name": "Alice"},
            {"type": "Person", "name": "Bob"},
            {"type": "Organization", "name": "MIT"},
        ]
        suggestions = TemplateExtractor.generate_suggestions_from_entities(entities)
        names = [s["name"] for s in suggestions]
        assert "Person" in names
        assert "Organization" in names

    def test_skips_unknown_type(self) -> None:
        entities = [{"type": "unknown", "name": "X"}]
        suggestions = TemplateExtractor.generate_suggestions_from_entities(entities)
        assert len(suggestions) == 0

    def test_an_item_is_the_object_core_type(self) -> None:
        # one template per core type (2026-10-08): "Item" is the core type Object itself
        entities = [{"type": "Item", "name": "X"}]
        suggestions = TemplateExtractor.generate_suggestions_from_entities(entities)
        assert [(s["name"], s["subtypes"]) for s in suggestions] == [("Object", {})]

    def test_counts_entities_per_type(self) -> None:
        entities = [
            {"type": "Person", "name": "A"},
            {"type": "Person", "name": "B"},
            {"type": "Person", "name": "C"},
        ]
        suggestions = TemplateExtractor.generate_suggestions_from_entities(entities)
        person_suggestion = next(s for s in suggestions if s["name"] == "Person")
        assert person_suggestion["entity_count"] == 3
        assert "3 entities" in person_suggestion["reason"]

    def test_sorted_by_count_descending(self) -> None:
        entities = [
            {"type": "Org", "name": "A"},
            {"type": "Person", "name": "B"},
            {"type": "Person", "name": "C"},
            {"type": "Person", "name": "D"},
        ]
        suggestions = TemplateExtractor.generate_suggestions_from_entities(entities)
        assert suggestions[0]["name"] == "Person"

    def test_empty_entities(self) -> None:
        assert TemplateExtractor.generate_suggestions_from_entities([]) == []

    def test_suggestions_have_icon_and_color(self) -> None:
        entities = [{"type": "Person", "name": "Alice"}]
        suggestions = TemplateExtractor.generate_suggestions_from_entities(entities)
        assert "icon" in suggestions[0]
        assert "color" in suggestions[0]

    def test_uses_domain_descriptions(self) -> None:
        # an old per-type name (Module) is its core type's (Work), described by the floor's template
        entities = [{"type": "Module", "name": "os"}]
        domain = [{"name": "Work", "description": "A created work"}]
        suggestions = TemplateExtractor.generate_suggestions_from_entities(entities, domain)
        assert suggestions[0]["name"] == "Work"
        assert suggestions[0]["subtypes"] == {"module": 1}
        assert suggestions[0]["description"] == "A created work"


# ============================================================================
# _extract_domain_descriptions
# ============================================================================


class TestExtractDomainDescriptions:
    """Tests for domain description extraction."""

    def test_builds_lookup(self) -> None:
        domain = [
            {"name": "Person", "description": "A human being"},
            {"name": "Org", "description": "An organization"},
        ]
        result = TemplateExtractor._extract_domain_descriptions(domain)
        assert result["person"] == "A human being"
        assert result["org"] == "An organization"

    def test_none_input(self) -> None:
        assert TemplateExtractor._extract_domain_descriptions(None) == {}

    def test_empty_input(self) -> None:
        assert TemplateExtractor._extract_domain_descriptions([]) == {}
