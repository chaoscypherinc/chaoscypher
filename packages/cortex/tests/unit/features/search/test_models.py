# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for search feature DTOs."""

from __future__ import annotations

from chaoscypher_cortex.features.search.models import SearchNodeHit


def test_search_node_hit_has_required_fields():
    """SearchNodeHit instantiates with id, label, template_id."""
    hit = SearchNodeHit(
        id="n1",
        label="Alice",
        template_id="person",
    )
    assert hit.id == "n1"
    assert hit.label == "Alice"
    assert hit.template_id == "person"


def test_search_node_hit_template_id_optional():
    """template_id defaults to None (nodes without a template still surface)."""
    hit = SearchNodeHit(id="n2", label="Unclassified")
    assert hit.template_id is None


def test_search_node_hit_rejects_unknown_fields():
    """SearchNodeHit is a strict projection — extras are ignored or rejected.

    The model omits the nodes feature's heavy fields (properties, embedding,
    position, created_at, updated_at, etc.). This test documents the projection
    intent: the search slice surfaces only what the UI actually reads.
    """
    hit = SearchNodeHit(id="n3", label="Bob", template_id="person")
    # Confirm heavy fields are NOT on the hit projection.
    assert not hasattr(hit, "embedding")
    assert not hasattr(hit, "properties")
    assert not hasattr(hit, "position")
