# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for EntityTemplateMatcher."""

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.engine.commit.matcher import EntityTemplateMatcher


@pytest.fixture
def matcher():
    """Create EntityTemplateMatcher with mock graph repository."""
    return EntityTemplateMatcher(graph_repository=MagicMock())


class TestMatch:
    """Tests for EntityTemplateMatcher.match."""

    def test_exact_match_in_source_templates(self, matcher) -> None:
        template_map = {"person": "tmpl_person", "organization": "tmpl_org"}
        result = matcher.match(
            entity_data={"type": "person", "name": "Alice"},
            all_templates=[],
            template_name_to_id=template_map,
        )
        assert result == "tmpl_person"

    def test_case_insensitive_match(self, matcher) -> None:
        template_map = {"person": "tmpl_person"}
        result = matcher.match(
            entity_data={"type": "PERSON", "name": "Alice"},
            all_templates=[],
            template_name_to_id=template_map,
        )
        assert result == "tmpl_person"

    def test_fallback_to_default_when_no_match(self, matcher) -> None:
        template_map = {"person": "tmpl_person"}
        result = matcher.match(
            entity_data={"type": "unknown_type", "name": "X"},
            all_templates=[],
            template_name_to_id=template_map,
        )
        assert result == "system_template_item"

    def test_fallback_when_no_template_map(self, matcher) -> None:
        result = matcher.match(
            entity_data={"type": "person", "name": "Alice"},
            all_templates=[],
            template_name_to_id=None,
        )
        assert result == "system_template_item"

    def test_fallback_when_empty_type(self, matcher) -> None:
        result = matcher.match(
            entity_data={"type": "", "name": "Alice"},
            all_templates=[],
            template_name_to_id={"person": "tmpl_person"},
        )
        assert result == "system_template_item"

    def test_fallback_when_missing_type(self, matcher) -> None:
        result = matcher.match(
            entity_data={"name": "Alice"},
            all_templates=[],
            template_name_to_id={"person": "tmpl_person"},
        )
        assert result == "system_template_item"


class TestFallbackSelfHeal:
    """The fallback path lazily upserts system_template_item if it's missing."""

    def test_fallback_skips_create_when_template_exists(self) -> None:
        """If get_template returns a row, no create_template call is made."""
        repo = MagicMock()
        repo.get_template.return_value = MagicMock(id="system_template_item")
        matcher = EntityTemplateMatcher(graph_repository=repo)

        result = matcher.match(
            entity_data={"type": "object", "name": "X"},
            all_templates=[],
            template_name_to_id={"person": "tmpl_person"},
        )

        assert result == "system_template_item"
        repo.get_template.assert_called_once_with("system_template_item")
        repo.create_template.assert_not_called()

    def test_fallback_recreates_template_when_missing(self) -> None:
        """If get_template returns None, the matcher upserts the default."""
        repo = MagicMock()
        repo.get_template.return_value = None
        recreated = MagicMock(id="system_template_item")
        repo.create_template.return_value = recreated
        matcher = EntityTemplateMatcher(graph_repository=repo)

        result = matcher.match(
            entity_data={"type": "object", "name": "X"},
            all_templates=[],
            template_name_to_id={"person": "tmpl_person"},
        )

        assert result == "system_template_item"
        repo.create_template.assert_called_once()
        # Verify create_template was given the canonical id and is_system=True.
        kwargs = repo.create_template.call_args.kwargs
        assert kwargs["custom_id"] == "system_template_item"
        assert kwargs["is_system"] is True

    def test_fallback_check_runs_at_most_once_per_matcher(self) -> None:
        """Repeated fallback hits don't re-issue get_template SELECTs."""
        repo = MagicMock()
        repo.get_template.return_value = MagicMock(id="system_template_item")
        matcher = EntityTemplateMatcher(graph_repository=repo)

        for entity_type in ("object", "thing", "blob", "widget"):
            matcher.match(
                entity_data={"type": entity_type},
                all_templates=[],
                template_name_to_id={"person": "tmpl_person"},
            )

        assert repo.get_template.call_count == 1

    def test_fallback_check_skipped_when_match_succeeds(self) -> None:
        """Hot path with a matching per-source template never touches get_template."""
        repo = MagicMock()
        matcher = EntityTemplateMatcher(graph_repository=repo)

        result = matcher.match(
            entity_data={"type": "person"},
            all_templates=[],
            template_name_to_id={"person": "tmpl_person"},
        )

        assert result == "tmpl_person"
        repo.get_template.assert_not_called()
        repo.create_template.assert_not_called()


class TestCaching:
    """Tests for template matching cache behavior."""

    def test_caches_result(self, matcher) -> None:
        template_map = {"person": "tmpl_person"}
        # First call
        result1 = matcher.match(
            entity_data={"type": "person"},
            all_templates=[],
            template_name_to_id=template_map,
        )
        # Second call should hit cache
        result2 = matcher.match(
            entity_data={"type": "person"},
            all_templates=[],
            template_name_to_id=template_map,
        )
        assert result1 == result2 == "tmpl_person"
        assert len(matcher._template_cache) >= 1

    def test_different_template_maps_different_cache_keys(self, matcher) -> None:
        map1 = {"person": "tmpl_1"}
        map2 = {"person": "tmpl_2"}
        result1 = matcher.match(
            entity_data={"type": "person"},
            all_templates=[],
            template_name_to_id=map1,
        )
        result2 = matcher.match(
            entity_data={"type": "person"},
            all_templates=[],
            template_name_to_id=map2,
        )
        assert result1 == "tmpl_1"
        assert result2 == "tmpl_2"
