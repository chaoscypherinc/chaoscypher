# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A short name with a fuller alias of its own never folds into another fuller name."""

from __future__ import annotations

from chaoscypher_core.services.graph.resolution.plan import plan_merges
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    NameRecord,
    same_entity,
)


def _rec(label: str, aliases: list[str] | None = None) -> NameRecord:
    return NameRecord(label=label, type="character", aliases=aliases or [])


def test_lise_with_her_own_full_name_is_not_princess_mary() -> None:
    lise = _rec("Princess Bolkónskaya", ["Lise Meinen", "the little princess"])
    mary = _rec("Princess Mary Bolkónskaya")
    assert same_entity(lise, mary) is None
    assert same_entity(mary, lise) is None


def test_a_diminutive_alias_does_not_block_a_real_merge() -> None:
    boris = _rec("Borís", ["Bóry"])
    full = _rec("Borís Drubetskóy")
    evidence = same_entity(boris, full)
    assert evidence is not None and evidence.kind == "subset"


def test_count_bezukhov_with_cyrils_full_name_is_not_pierre() -> None:
    count = _rec("Count Bezúkhov", ["Cyril Vladímirovich Bezúkhov"])
    pierre = _rec("Pierre Bezúkhov")
    assert same_entity(count, pierre) is None
    cyril = _rec("Cyril Vladímirovich Bezúkhov")
    evidence = same_entity(count, cyril)
    assert evidence is not None  # the alias names him: an alias match, not a subset


def test_the_resolver_follows_the_same_rule() -> None:
    nodes = [
        {
            "id": "l",
            "label": "Princess Bolkónskaya",
            "type": "character",
            "aliases": ["Lise Meinen"],
            "properties": {},
        },
        {
            "id": "m",
            "label": "Princess Mary Bolkónskaya",
            "type": "character",
            "aliases": [],
            "properties": {},
        },
        {
            "id": "d",
            "label": "Princess Drubetskáya",
            "type": "character",
            "aliases": [],
            "properties": {},
        },
        {
            "id": "a",
            "label": "Princess Anna Mikháylovna Drubetskáya",
            "type": "character",
            "aliases": [],
            "properties": {},
        },
    ]
    assert {(m.keep, m.drop) for m in plan_merges(nodes, [])} == {("a", "d")}
