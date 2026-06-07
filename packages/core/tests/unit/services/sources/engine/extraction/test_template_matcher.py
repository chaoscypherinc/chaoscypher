# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the edge template suggestion wrapper in template_matcher."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.engine.extraction.template_matcher import (
    suggest_edge_templates,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rel(rel_type: str) -> dict[str, str]:
    """Create a minimal relationship dict for tests."""
    return {"type": rel_type, "from": "a", "to": "b"}


# ---------------------------------------------------------------------------
# TestSuggestEdgeTemplates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuggestEdgeTemplates:
    """Tests for suggest_edge_templates()."""

    def test_empty_relationships_returns_empty(self) -> None:
        """An empty relationships list short-circuits to an empty result."""
        resolver = MagicMock()
        result = suggest_edge_templates([], get_domain_edge_templates=resolver)
        assert result == []
        resolver.assert_not_called()

    def test_generates_suggestion_for_single_type(self) -> None:
        """A single relationship type produces one suggestion."""
        result = suggest_edge_templates(
            [_rel("authored_by"), _rel("authored_by")],
            get_domain_edge_templates=lambda _: None,
        )
        assert len(result) == 1
        assert result[0]["name"] == "authored_by"
        assert result[0]["relationship_count"] == 2

    def test_generic_types_are_filtered(self) -> None:
        """Generic relationship types are dropped from suggestions."""
        result = suggest_edge_templates(
            [_rel("related"), _rel("link"), _rel("cites")],
            get_domain_edge_templates=lambda _: None,
        )
        names = [s["name"] for s in result]
        assert "related" not in names
        assert "link" not in names
        assert "cites" in names

    def test_domain_lookup_called_when_domain_provided(self) -> None:
        """get_domain_edge_templates is invoked with the detected domain name."""
        resolver = MagicMock(return_value=None)
        suggest_edge_templates(
            [_rel("authored_by")],
            detected_domain="literary",
            get_domain_edge_templates=resolver,
        )
        resolver.assert_called_once_with("literary")

    def test_domain_lookup_skipped_when_domain_none(self) -> None:
        """When detected_domain is None, the resolver is not invoked."""
        resolver = MagicMock()
        suggest_edge_templates(
            [_rel("authored_by")],
            detected_domain=None,
            get_domain_edge_templates=resolver,
        )
        resolver.assert_not_called()

    def test_domain_descriptions_applied_to_suggestions(self) -> None:
        """Descriptions from the domain override fallback descriptions."""
        domain_edges = [
            {"name": "authored_by", "description": "Author of a literary work"},
        ]
        result = suggest_edge_templates(
            [_rel("authored_by")],
            detected_domain="literary",
            get_domain_edge_templates=lambda _: domain_edges,
        )
        assert result[0]["description"] == "Author of a literary work"

    def test_resolver_exception_is_swallowed(self) -> None:
        """Any exception in the resolver causes an empty result, not a crash."""

        def _raiser(_: str) -> list[dict[str, str]]:
            raise RuntimeError("domain lookup blew up")

        result = suggest_edge_templates(
            [_rel("authored_by")],
            detected_domain="literary",
            get_domain_edge_templates=_raiser,
        )
        assert result == []

    def test_count_aggregation_across_types(self) -> None:
        """Repeated relationship types aggregate counts and sort by frequency."""
        rels = [
            _rel("authored_by"),
            _rel("published_by"),
            _rel("authored_by"),
            _rel("authored_by"),
            _rel("published_by"),
        ]
        result = suggest_edge_templates(rels, get_domain_edge_templates=lambda _: None)
        counts = {s["name"]: s["relationship_count"] for s in result}
        assert counts["authored_by"] == 3
        assert counts["published_by"] == 2
        # Sorted by count descending
        assert result[0]["name"] == "authored_by"
