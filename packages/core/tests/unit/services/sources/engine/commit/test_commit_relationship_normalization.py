# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests: commit must normalize relationships reloaded from the
relational store (migration 0042) before the orphan filter runs.

``list_source_relationships`` → ``_relationship_row_to_dict`` projects each
``SourceRelationship`` row with ``source``/``target`` holding STRING entity
IDs (``rel.source_entity_id`` / ``rel.target_entity_id``) plus ``from``/``to``
names. But ``drop_orphan_entities`` and ``relation.py``'s endpoint resolver
expect ``source``/``target`` to be INTEGER indices into the entities list.

Before this fix, the CLI ``source add`` commit path and the core recovery
re-commit path both rebuilt ``commit_data`` straight from those reloaded rows,
so every relationship's ``isinstance(source, int)`` check failed, the
referenced-entity set was always empty, and 100% of entities were dropped as
false orphans — committing an empty graph for every source.

``normalize_relationship_endpoints`` converts id-keyed endpoints back to the
integer-index contract; integer endpoints (the in-memory finalizer path) pass
through untouched.
"""

from __future__ import annotations

from chaoscypher_core.services.sources.engine.commit.service import (
    drop_orphan_entities,
    normalize_relationship_endpoints,
)


def _reload_shape_rel(rel_id, src_id, tgt_id, src_name, tgt_name, predicate):
    """Mirror ``_relationship_row_to_dict``: source/target are STRING ids."""
    return {
        "id": rel_id,
        "source": src_id,
        "target": tgt_id,
        "predicate": predicate,
        "type": predicate,
        "confidence": 0.9,
        "from": src_name,
        "to": tgt_name,
    }


def test_string_entity_id_endpoints_become_integer_indices() -> None:
    """`source`/`target` string entity ids map to entity list positions."""
    entities = [
        {"id": "ent_a", "name": "Alice"},
        {"id": "ent_b", "name": "Bob"},
    ]
    relationships = [
        _reload_shape_rel("rel_1", "ent_a", "ent_b", "Alice", "Bob", "knows"),
    ]

    normalized = normalize_relationship_endpoints(entities, relationships)

    assert normalized[0]["source"] == 0
    assert normalized[0]["target"] == 1
    # Names preserved for the downstream name-based resolver.
    assert normalized[0]["from"] == "Alice"
    assert normalized[0]["to"] == "Bob"


def test_integer_endpoints_pass_through_unchanged() -> None:
    """Finalizer-path relationships (already integer indices) are untouched."""
    entities = [{"id": "ent_a", "name": "Alice"}, {"id": "ent_b", "name": "Bob"}]
    relationships = [{"source": 0, "target": 1, "type": "knows"}]

    normalized = normalize_relationship_endpoints(entities, relationships)

    assert normalized[0]["source"] == 0
    assert normalized[0]["target"] == 1


def test_unknown_endpoint_id_left_unchanged() -> None:
    """A `source`/`target` id absent from entities is left as-is, not re-indexed."""
    entities = [{"id": "ent_a", "name": "Alice"}]
    relationships = [
        _reload_shape_rel("rel_1", "ent_a", "ent_missing", "Alice", "Ghost", "knows"),
    ]

    normalized = normalize_relationship_endpoints(entities, relationships)

    assert normalized[0]["source"] == 0
    assert normalized[0]["target"] == "ent_missing"


def test_reload_shape_entities_survive_orphan_filter() -> None:
    """Relational-reload relationships must NOT drop every connected entity.

    The production regression: with id-keyed endpoints, the orphan filter
    classified every entity as an orphan and committed an empty graph.
    """
    entities = [
        {"id": "ent_a", "name": "Alice"},
        {"id": "ent_b", "name": "Bob"},
        {"id": "ent_c", "name": "Carol"},  # genuine orphan (no relationship)
    ]
    relationships = [
        _reload_shape_rel("rel_1", "ent_a", "ent_b", "Alice", "Bob", "knows"),
    ]

    normalized = normalize_relationship_endpoints(entities, relationships)
    kept, _remapped, dropped = drop_orphan_entities(entities, normalized, enabled=True)

    assert {e["id"] for e in kept} == {"ent_a", "ent_b"}
    assert dropped == 1  # only Carol, the real orphan
