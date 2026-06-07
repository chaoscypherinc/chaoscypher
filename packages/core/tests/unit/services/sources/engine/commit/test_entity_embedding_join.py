# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Entity embeddings must be joined to nodes by stable identity, not list position.

Regression for the orphan-filter index-shift mis-join: stored entity
embeddings are keyed by ``entity_index`` = the entity's position in the
*pre*-orphan-filter extraction list. The default ``balanced`` mode runs
``drop_orphan_entities`` before ``prepare_entity_nodes``, which compacts
the entity list and re-indexes survivors. Joining embeddings by the
*post*-filter positional index therefore assigns every entity after a
dropped orphan a DIFFERENT entity's embedding vector — silent corruption
of the semantic-search vectors with no error and no quality counter.

The fix joins embeddings by ``entity_id`` (the stable id that
``normalize_entities`` stamps on every entity and ``store_entity_embeddings``
persists alongside each vector), so the join survives any index-changing
pipeline step.
"""

from __future__ import annotations

from typing import Any

from chaoscypher_core.services.sources.engine.commit.entity import EntityCommitHandler


class _FakeEmbeddingRepo:
    """Returns stored embeddings keyed by the pre-filter ``entity_index``."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def get_entity_embeddings(self, source_id: str) -> list[dict[str, Any]]:
        return self._rows


class _FakeMatcher:
    """Resolves every entity to the default system template."""

    def match(self, *_args: Any, **_kwargs: Any) -> str:
        return "system_template_item"


def _make_handler(rows: list[dict[str, Any]]) -> EntityCommitHandler:
    return EntityCommitHandler(
        graph_repository=None,  # unused by prepare_entity_nodes
        source_repository=_FakeEmbeddingRepo(rows),
        entity_matcher=_FakeMatcher(),
        database_name="test",
    )


def test_embeddings_follow_their_entity_after_orphan_drop() -> None:
    """A survivor shifted by a dropped orphan keeps ITS OWN embedding vector."""
    # Pre-filter extraction order was [Alpha, Beta(orphan), Gamma]; embeddings
    # were stored keyed by that pre-filter index (0, 1, 2).
    rows = [
        {"entity_index": 0, "entity_id": "ent_alpha", "embedding": [1.0, 0.0, 0.0]},
        {"entity_index": 1, "entity_id": "ent_beta", "embedding": [0.0, 1.0, 0.0]},
        {"entity_index": 2, "entity_id": "ent_gamma", "embedding": [0.0, 0.0, 1.0]},
    ]
    handler = _make_handler(rows)

    # The orphan filter dropped Beta; the commit list is the COMPACTED survivors,
    # so Gamma now sits at index 1 (formerly 2).
    compacted_entities = [
        {"id": "ent_alpha", "name": "Alpha", "type": "Person"},
        {"id": "ent_gamma", "name": "Gamma", "type": "Person"},
    ]

    nodes, _entity_data, _template_ids = handler.prepare_entity_nodes(
        compacted_entities,
        all_templates=[],
        suggested_templates=None,
        template_name_to_id={},
        file_info={},
        file_id="src-1",
        source_id="src-1",
    )

    by_label = {n.label: n.embedding for n in nodes}
    assert by_label["Alpha"] == [1.0, 0.0, 0.0]
    # The bug assigned Gamma the vector at post-filter index 1 — Beta's [0,1,0].
    assert by_label["Gamma"] == [0.0, 0.0, 1.0]


def test_embeddings_join_is_robust_to_reordering() -> None:
    """Join is by id, not position — reordered survivors still match their vectors."""
    rows = [
        {"entity_index": 0, "entity_id": "ent_a", "embedding": [1.0, 0.0]},
        {"entity_index": 1, "entity_id": "ent_b", "embedding": [0.0, 1.0]},
    ]
    handler = _make_handler(rows)

    # Survivors arrive in reversed order relative to storage.
    entities = [
        {"id": "ent_b", "name": "Bravo", "type": "Person"},
        {"id": "ent_a", "name": "Alfa", "type": "Person"},
    ]

    nodes, _data, _ids = handler.prepare_entity_nodes(
        entities,
        all_templates=[],
        suggested_templates=None,
        template_name_to_id={},
        file_info={},
        file_id="src-2",
        source_id="src-2",
    )

    by_label = {n.label: n.embedding for n in nodes}
    assert by_label["Bravo"] == [0.0, 1.0]
    assert by_label["Alfa"] == [1.0, 0.0]
