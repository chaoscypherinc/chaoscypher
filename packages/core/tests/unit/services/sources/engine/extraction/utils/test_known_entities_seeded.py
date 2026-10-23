# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The KNOWN ENTITIES list seeded from the graph: the memory path's list (spec decision 2).

A memory is one chunk, so the running list would be empty when its one group is extracted;
seeded with the graph's matching nodes and the speaker, a new memory's "Pierre" lands on the
Pierre node. The unseeded list, the document path's, is unchanged.
"""

from __future__ import annotations

from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
    KNOWN_ENTITIES_HEADER,
    KNOWN_GRAPH_HEADER,
    KnownEntities,
)
from chaoscypher_core.settings import EngineSettings


def test_a_seeded_list_renders_the_graph_header_and_the_seed_before_any_group() -> None:
    known = KnownEntities.for_plan(
        {},
        EngineSettings(),
        seed=[
            {"name": "Pierre Bezukhov", "type": "Person", "aliases": ["Pierre"], "properties": {}}
        ],
    )
    assert known.block().startswith(KNOWN_GRAPH_HEADER + "\n- Pierre Bezukhov (Person)")
    assert known.groups == 0


def test_the_speaker_line_carries_the_role_phrase() -> None:
    known = KnownEntities.for_plan(
        {},
        EngineSettings(),
        seed=[
            {
                "name": "Alice",
                "type": "Person",
                "aliases": [],
                "properties": {"role": "the speaker (I, me)"},
            }
        ],
    )
    assert "- Alice (Person) — the speaker (I, me)" in known.block()


def test_an_unseeded_list_is_unchanged() -> None:
    known = KnownEntities.for_plan({}, EngineSettings())
    assert known.block() == ""
    known.add_group([{"name": "Pierre", "type": "Person", "aliases": []}], [])
    assert known.block().startswith(KNOWN_ENTITIES_HEADER + "\n")
    assert KNOWN_GRAPH_HEADER != KNOWN_ENTITIES_HEADER


def test_a_group_after_the_seed_joins_the_seeded_entry() -> None:
    known = KnownEntities.for_plan(
        {},
        EngineSettings(),
        seed=[
            {"name": "Pierre Bezukhov", "type": "Person", "aliases": ["Pierre"], "properties": {}}
        ],
    )
    known.add_group([{"name": "Pierre", "type": "Person", "aliases": []}], [])
    assert known.groups == 1
    assert len(known) == 1
