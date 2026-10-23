# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests: ``flag_orphan_entities`` must work against the real
extraction relationship shape, not a synthetic one; and keep, flag, never delete.

The upstream extraction pipeline emits relationships keyed by integer
indices into the ``entities`` list — each relationship has ``source: int``
and ``target: int`` whose values position into ``entities``. The
downstream consumers (``commit/relation.py:157`` and
``commit/service.py:1120``) resolve endpoints via those indices, so any
filter that runs before node creation must use the same contract.

Regression: an earlier implementation of ``flag_orphan_entities`` read
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
    flag_orphan_entities,
)
from chaoscypher_core.services.sources.engine.deduplication.name_identity import ORPHAN_KEY


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

    kept, _remapped, _flagged, dropped_count = flag_orphan_entities(
        entities, relationships, keep_evidence_less=False
    )

    assert [e["name"] for e in kept] == ["Alice", "Bob"]
    assert dropped_count == 1


def test_keeps_orphans_when_disabled() -> None:
    """When the filter is disabled, every entity passes through unchanged."""
    entities = [{"name": "Alice"}, {"name": "Carol"}]
    relationships: list[dict] = []

    kept, remapped, _flagged, dropped_count = flag_orphan_entities(
        entities, relationships, keep_evidence_less=True
    )

    assert [e["name"] for e in kept] == ["Alice", "Carol"]
    assert remapped == []
    assert dropped_count == 0


def test_drops_all_entities_when_no_relationships_and_enabled() -> None:
    """All-orphan corner case: zero relationships + enabled drops everything."""
    entities = [{"name": "Alice"}, {"name": "Bob"}]
    relationships: list[dict] = []

    kept, _remapped, _flagged, dropped_count = flag_orphan_entities(
        entities, relationships, keep_evidence_less=False
    )

    assert kept == []
    assert dropped_count == 2


def test_keeps_entity_referenced_only_as_target() -> None:
    """An entity referenced only as `target` is still kept (not just `source`)."""
    entities = [{"name": "Alice"}, {"name": "Bob"}, {"name": "Carol"}]
    relationships = [
        {"source": 0, "target": 2, "type": "mentions"},  # Carol via target only
    ]

    kept, _remapped, _flagged, _dropped = flag_orphan_entities(
        entities, relationships, keep_evidence_less=False
    )

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

    kept, _remapped, _flagged, dropped_count = flag_orphan_entities(
        entities, relationships, keep_evidence_less=False
    )

    # Entities 0,1,2,3 referenced; entity 4 orphan.
    assert {e["id"] for e in kept} == {"entity_0", "entity_1", "entity_2", "entity_3"}
    assert dropped_count == 1


def test_ignores_non_integer_source_target() -> None:
    """Defensive: malformed relationships (string/None endpoints) are skipped, not crashed.

    Out-of-band relationship payloads must not classify a valid entity as
    orphan-by-typo; the filter only acts on integer-typed indices.
    """
    entities = [{"name": "Alice"}, {"name": "Bob"}]
    relationships: list[dict] = [
        {"source": "Alice", "target": "Bob", "type": "knows"},  # legacy/garbage
        {"source": None, "target": None},
        {"source": 0, "target": 1, "type": "knows"},  # the only valid one
    ]

    kept, _remapped, _flagged, _dropped = flag_orphan_entities(
        entities, relationships, keep_evidence_less=False
    )

    assert {e["name"] for e in kept} == {"Alice", "Bob"}


# --------------------------------------------------------------------------- keep, flag, never delete


def _flag(entity: dict) -> object:
    return (entity.get("properties") or {}).get(ORPHAN_KEY)


def test_an_orphan_that_carries_evidence_is_kept_flagged() -> None:
    """Keep, flag, never delete (2026-10-07): a description or an evidence sentence keeps an orphan.

    The v4b generic arm's commit deleted "Márya Lvóvna Karágina", whose
    description holds that she is Julie's mother, because the relationship
    pass wrote no edge for her.
    """
    entities: list[dict] = [
        {"name": "Julie Karágina"},
        {"name": "Nicholas"},
        {"name": "Márya Lvóvna Karágina", "description": "Julie's mother."},
        {"name": "the footman", "evidence": [{"chunk_id": "c1", "text": "announced the footman"}]},
        {"name": "the hostess", "evidence": [{"chunk_id": "c1"}, {"chunk_id": "c2", "text": " "}]},
    ]
    relationships: list[dict] = [{"source": 0, "target": 1, "type": "interacts_with"}]

    out = flag_orphan_entities(entities, relationships, keep_evidence_less=False)

    assert [e["name"] for e in out.entities] == [
        "Julie Karágina",
        "Nicholas",
        "Márya Lvóvna Karágina",
        "the footman",
    ]
    assert [_flag(e) for e in out.entities] == [None, None, True, True]
    assert (out.flagged, out.dropped) == (2, 1)
    assert out.entities[2]["description"] == "Julie's mother."
    assert out.relationships == relationships
    assert "properties" not in entities[2]  # the input is not mutated


def test_protect_orphans_keeps_an_evidence_less_orphan_flagged() -> None:
    entities = [{"name": "Alice"}, {"name": "Bob"}, {"name": "Carol"}]
    out = flag_orphan_entities(
        entities, [{"source": 0, "target": 1, "type": "knows"}], keep_evidence_less=True
    )
    assert [_flag(e) for e in out.entities] == [None, None, True]
    assert (out.flagged, out.dropped) == (1, 0)


def test_the_flag_is_the_commits_a_stale_one_is_cleared_and_the_texts_value_kept() -> None:
    """A connected entity loses a stale ``True``; an extracted ``orphan`` of another value stays."""
    entities: list[dict] = [
        {"name": "Sónya", "properties": {"orphan": "yes, raised by the Rostóvs"}},
        {"name": "Nicholas", "properties": {ORPHAN_KEY: True, "title": "Count"}},
    ]
    out = flag_orphan_entities(
        entities, [{"source": 0, "target": 1, "type": "knows"}], keep_evidence_less=False
    )
    assert out.entities[0] is entities[0]
    assert out.entities[1]["properties"] == {"title": "Count"}
    assert out.flagged == 0


def test_a_nested_entity_data_record_is_read_and_flagged() -> None:
    """The legacy nested shape (``entity_data``) carries its description and gets its flag there."""
    entities: list[dict] = [
        {"action": "create", "entity_data": {"name": "A"}},
        {"action": "create", "entity_data": {"name": "B", "description": "A guest."}},
        {"action": "create", "entity_data": {"name": "C"}},
    ]
    out = flag_orphan_entities(entities, [], keep_evidence_less=False)
    assert [e["entity_data"]["name"] for e in out.entities] == ["B"]
    assert out.entities[0]["entity_data"]["properties"] == {ORPHAN_KEY: True}
    assert out.entities[0]["action"] == "create"
    assert (out.flagged, out.dropped) == (1, 2)
