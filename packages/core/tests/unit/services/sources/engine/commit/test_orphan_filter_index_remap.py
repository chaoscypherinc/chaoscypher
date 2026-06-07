# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``drop_orphan_entities`` must remap relationship indices to surviving entities.

Workstream 3, Tasks 3.3+3.4: when the orphan filter removes an entity,
every entity after it shifts down by one. Downstream
``prepare_relationship_edges`` and citation creation key into the
filtered entity list by integer index, so they need relationships that
also reference those new indices. The pre-fix filter returned
``(kept, dropped)`` but left relationships unchanged — edges into
kept-but-shifted entities silently disappeared at commit time.

The fix borrows ``EntityProcessor.remap_relationship_indices`` (the
canonical remap pattern already used by dedup and type-rescue) and
returns ``(kept, remapped_relationships, dropped_count)``.
"""

from __future__ import annotations

from chaoscypher_core.services.sources.engine.commit.service import (
    drop_orphan_entities,
)


def test_orphan_filter_remaps_indices_into_surviving_entities() -> None:
    """When index 1 is dropped, an edge into index 2 must remap to new index 1."""
    entities = [
        {"name": "Alice", "type": "Person"},  # idx 0 — has edge
        {"name": "Orphan", "type": "Concept"},  # idx 1 — no edges, orphan
        {"name": "Bob", "type": "Person"},  # idx 2 — has edge (target)
    ]
    relationships = [
        {"source": 0, "target": 2, "type": "knows"},  # Alice -> Bob
    ]

    kept, remapped, dropped_count = drop_orphan_entities(entities, relationships, enabled=True)

    # Both Alice and Bob committed; Orphan dropped.
    names = [e["name"] for e in kept]
    assert names == ["Alice", "Bob"]
    assert dropped_count == 1

    # The Alice -> Bob edge survived AND its target now references the
    # post-filter index (1, formerly 2). Without the fix, target stays
    # at 2 and the downstream lookup against the filtered entity list
    # silently drops the edge.
    assert len(remapped) == 1
    edge = remapped[0]
    assert edge["source"] == 0  # Alice still at 0
    assert edge["target"] == 1  # Bob shifted from 2 -> 1
    assert edge["type"] == "knows"


def test_orphan_filter_preserves_signature_when_disabled() -> None:
    """When the filter is disabled, returns inputs unchanged with zero dropped."""
    entities = [{"name": "Alice"}, {"name": "Carol"}]
    relationships = [{"source": 0, "target": 1, "type": "knows"}]

    kept, remapped, dropped_count = drop_orphan_entities(entities, relationships, enabled=False)

    assert kept == entities
    assert remapped == relationships
    assert dropped_count == 0


def test_orphan_filter_drops_relationships_into_removed_entity() -> None:
    """Edges that reference an orphaned entity are dropped, not silently mis-pointed."""
    entities = [
        {"name": "Alice"},
        {"name": "Orphan"},  # orphan — referenced by no relationship
        {"name": "Bob"},
    ]
    relationships = [
        {"source": 0, "target": 2, "type": "knows"},  # Alice -> Bob
    ]
    kept, remapped, dropped_count = drop_orphan_entities(entities, relationships, enabled=True)

    # Sanity: Bob shifted from 2 -> 1.
    assert [e["name"] for e in kept] == ["Alice", "Bob"]
    assert dropped_count == 1
    assert remapped[0]["source"] == 0
    assert remapped[0]["target"] == 1


def test_orphan_filter_handles_multiple_drops() -> None:
    """Two consecutive orphans → indices 3 and 4 should remap to 1 and 2."""
    entities = [
        {"name": "A"},  # idx 0 — kept
        {"name": "Orph1"},  # idx 1 — orphan
        {"name": "Orph2"},  # idx 2 — orphan
        {"name": "B"},  # idx 3 — kept
        {"name": "C"},  # idx 4 — kept
    ]
    relationships = [
        {"source": 0, "target": 3, "type": "knows"},  # A -> B
        {"source": 3, "target": 4, "type": "knows"},  # B -> C
    ]
    kept, remapped, dropped_count = drop_orphan_entities(entities, relationships, enabled=True)

    assert [e["name"] for e in kept] == ["A", "B", "C"]
    assert dropped_count == 2

    # A stays at 0, B remaps 3 -> 1, C remaps 4 -> 2.
    assert {(r["source"], r["target"]) for r in remapped} == {(0, 1), (1, 2)}
