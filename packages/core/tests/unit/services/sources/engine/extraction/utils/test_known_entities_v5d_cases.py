# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The three conflations the v5d pack recorded, through the guards (spec § Findings 1).

The E| lines are the pass-1 records the research decoded from the pack's store
(descriptions shortened). They use the corpus's names on purpose: they are
recorded test data, never prompt text.
"""

from __future__ import annotations

from typing import Any

from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import KnownEntities
from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
    parse_entity_line,
)


NAME_DAY_GROUP = [
    "E|Count Cyril Vladímirovich|Person/character|the count; Ilyá|1.0|S2-S21|A count who is currently ill. He is the husband of the countess.",
    "E|the countess|Person/character|the mother|1.0|S3-S20|The wife of Count Cyril Vladímirovich. She is the mother of Natásha.",
]
VERA_GROUP = [
    "E|Anna Mikháylovna|Person/character||1.0|S1-S4|A woman who is the mother of Véra. She is addressed as Mamma by her daughter.",
    "E|Véra|Person/character||1.0|S2-S3|The eldest daughter of Anna Mikháylovna.",
]
ANNETTE_GROUP = [
    "E|Annette|Person/character||0.9|S5|A person who travels alone in a carriage.",
    "E|Anna Mikháylovna|Person/character||1.0|S1-S6|A princess who asks for help.",
]
SENTENCES_NAME_DAY = [
    "[S1] x",
    "[S2] He has come to see Count Cyril Vladímirovich.",
    "[S3] 'You spoil her, Ilyá,' she added, turning to her husband.",
] + [f"[S{i}] ." for i in range(4, 22)]


def _parse(lines: list[str]) -> list[dict[str, Any]]:
    records = [parse_entity_line(line) for line in lines]
    assert all(r is not None for r in records)
    return [r for r in records if r is not None]


def _known_book_one_so_far() -> KnownEntities:
    known = KnownEntities()
    known.add_group(
        [
            {
                "name": "Count Ilyá Rostóv",
                "type": "Person",
                "aliases": ["the count", "Ilyá"],
                "properties": {},
            },
            {
                "name": "Countess Rostóva",
                "type": "Person",
                "aliases": ["the countess"],
                "properties": {},
            },
            {
                "name": "Count Cyril Vladímirovich Bezúkhov",
                "type": "Person",
                "aliases": ["Count Bezúkhov", "Count Cyril Vladímirovich"],
                "properties": {},
            },
            {
                "name": "Anna Pávlovna Schérer",
                "type": "Person",
                "aliases": ["Annette"],
                "properties": {},
            },
            {"name": "Anna Mikháylovna", "type": "Person", "aliases": [], "properties": {}},
        ],
        [],
    )
    return known


def test_the_name_day_conflation_loses_the_crossed_alias_and_keeps_its_sentence() -> None:
    records = _parse(NAME_DAY_GROUP)
    out, stats = _known_book_one_so_far().guard(records, SENTENCES_NAME_DAY)
    cyril = out[0]
    assert "Ilyá" not in cyril["aliases"] and cyril["properties"]["alias_crossed_known"] == "Ilyá"
    # the sentence is the model's: never moved by code
    assert "husband of the countess" in cyril["description"]
    assert stats.aliases_crossed == 1


def test_annette_cannot_be_given_as_anna_mikhaylovnas_alias() -> None:
    records = _parse(ANNETTE_GROUP)
    records[1]["aliases"] = [
        "Annette"
    ]  # the fold the exact dedup would make from pass 2's instance_of
    out, stats = _known_book_one_so_far().guard(records, [f"[S{i}] ." for i in range(1, 7)])
    assert out[1]["aliases"] == [] and stats.aliases_crossed == 1
    assert out[0]["name"] == "Annette"  # Annette stays a record of its own


def test_the_vera_group_crosses_nothing() -> None:
    records = _parse(VERA_GROUP)
    out, stats = _known_book_one_so_far().guard(
        records, [f"[S{i}] Anna Mikháylovna and Véra." for i in range(1, 5)]
    )
    assert [r["aliases"] for r in out] == [[], []] and stats.aliases_crossed == 0
