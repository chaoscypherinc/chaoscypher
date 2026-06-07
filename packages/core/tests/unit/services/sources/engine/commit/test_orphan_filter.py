# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests: ``drop_orphan_entities`` must work against the real
extraction relationship shape, not a synthetic one.

The upstream extraction pipeline emits relationships keyed by integer
indices into the ``entities`` list — each relationship has ``source: int``
and ``target: int`` whose values position into ``entities``. The
downstream consumers (``commit/relation.py:157`` and
``commit/service.py:1120``) resolve endpoints via those indices, so any
filter that runs before node creation must use the same contract.

Regression: an earlier implementation of ``drop_orphan_entities`` read
``rel["source_entity"]`` / ``rel["target_entity"]`` expecting string
names. Those keys never appear in production extraction output, so the
referenced-set was always empty and every entity was classified orphan
and silently dropped. The previous tests in this file used the same
invented shape — they passed in isolation while production destroyed
all entities for every committed source. This file replaces them with
real-shape fixtures.
"""

from __future__ import annotations

from chaoscypher_core.services.sources.engine.commit.service import (
    drop_orphan_entities,
)


def test_drops_entity_with_no_relationship_when_enabled() -> None:
    """Entities not referenced by any relationship index are dropped."""
    entities = [
        {"name": "Alice"},  # index 0 — referenced
        {"name": "Bob"},  # index 1 — referenced
        {"name": "Carol"},  # index 2 — orphan
    ]
    relationships = [
        {"source": 0, "target": 1, "type": "knows"},
    ]

    kept, _remapped, dropped_count = drop_orphan_entities(entities, relationships, enabled=True)

    assert [e["name"] for e in kept] == ["Alice", "Bob"]
    assert dropped_count == 1


def test_keeps_orphans_when_disabled() -> None:
    """When the filter is disabled, every entity passes through unchanged."""
    entities = [{"name": "Alice"}, {"name": "Carol"}]
    relationships: list[dict] = []

    kept, remapped, dropped_count = drop_orphan_entities(entities, relationships, enabled=False)

    assert [e["name"] for e in kept] == ["Alice", "Carol"]
    assert remapped == []
    assert dropped_count == 0


def test_drops_all_entities_when_no_relationships_and_enabled() -> None:
    """All-orphan corner case: zero relationships + enabled drops everything."""
    entities = [{"name": "Alice"}, {"name": "Bob"}]
    relationships: list[dict] = []

    kept, _remapped, dropped_count = drop_orphan_entities(entities, relationships, enabled=True)

    assert kept == []
    assert dropped_count == 2


def test_keeps_entity_referenced_only_as_target() -> None:
    """An entity referenced only as `target` is still kept (not just `source`)."""
    entities = [{"name": "Alice"}, {"name": "Bob"}, {"name": "Carol"}]
    relationships = [
        {"source": 0, "target": 2, "type": "mentions"},  # Carol via target only
    ]

    kept, _remapped, _dropped = drop_orphan_entities(entities, relationships, enabled=True)

    assert {e["name"] for e in kept} == {"Alice", "Carol"}


def test_handles_real_extraction_shape() -> None:
    """Smoke test against the exact shape the extraction pipeline emits.

    Mirrors the real ``extraction_results`` payload shape captured from a
    live import: nested ``entity_data`` dicts (with ``id``/``name``/``type``
    plus extras) and relationships keyed by integer ``source``/``target``
    indices alongside per-relationship metadata.
    """
    entities = [
        {"id": "entity_0", "name": "War and Peace", "type": "Literary Work"},
        {"id": "entity_1", "name": "Leo Tolstoy", "type": "Author"},
        {"id": "entity_2", "name": "Anna Pávlovna Schérer", "type": "Character"},
        {"id": "entity_3", "name": "Prince Vasíli Kurágin", "type": "Character"},
        {"id": "entity_4", "name": "Empress Márya Fëdorovna", "type": "Character"},
    ]
    relationships = [
        {
            "source": 2,
            "target": 3,
            "type": "interacts_with",
            "chunk_index": 0,
            "confidence": 0.9,
            "justification": "...",
            "sent_ref": "...",
        },
        {
            "source": 0,
            "target": 1,
            "type": "authored_by",
            "chunk_index": 0,
            "confidence": 0.95,
        },
    ]

    kept, _remapped, dropped_count = drop_orphan_entities(entities, relationships, enabled=True)

    # Entities 0,1,2,3 referenced; entity 4 orphan.
    assert {e["id"] for e in kept} == {"entity_0", "entity_1", "entity_2", "entity_3"}
    assert dropped_count == 1


def test_ignores_non_integer_source_target() -> None:
    """Defensive: malformed relationships (string/None endpoints) are skipped, not crashed.

    Out-of-band relationship payloads must not classify a valid entity as
    orphan-by-typo; the filter only acts on integer-typed indices.
    """
    entities = [{"name": "Alice"}, {"name": "Bob"}]
    relationships = [
        {"source": "Alice", "target": "Bob", "type": "knows"},  # legacy/garbage
        {"source": None, "target": None},
        {"source": 0, "target": 1, "type": "knows"},  # the only valid one
    ]

    kept, _remapped, _dropped = drop_orphan_entities(entities, relationships, enabled=True)

    assert {e["name"] for e in kept} == {"Alice", "Bob"}
