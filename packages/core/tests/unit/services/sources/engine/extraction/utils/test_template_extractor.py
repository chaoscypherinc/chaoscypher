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

    def test_skips_item_type(self) -> None:
        entities = [{"type": "Item", "name": "X"}]
        suggestions = TemplateExtractor.generate_suggestions_from_entities(entities)
        assert len(suggestions) == 0

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
        entities = [{"type": "Module", "name": "os"}]
        domain = [{"name": "Module", "description": "A Python module"}]
        suggestions = TemplateExtractor.generate_suggestions_from_entities(entities, domain)
        assert suggestions[0]["description"] == "A Python module"


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
