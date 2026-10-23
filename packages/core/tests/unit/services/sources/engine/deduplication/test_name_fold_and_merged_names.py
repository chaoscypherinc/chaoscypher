# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The name fold, and the names a merge folds in, through the real callers (audit W4-14, W4-3).

``fold`` kept only ``[a-z0-9]``: a Cyrillic or CJK name had no word at all,
``ø`` and ``ł`` split a word in two, and a hyphenated given name split into
two given names (``Anne-Marie Dupont`` held ``Marie Dupont``). And a merge's
folded-in names were invisible to every later pass: a semantic merge of
``Princess Bolkónskaya`` and ``Princess Mary Bolkónskaya`` was then folded
by the hierarchical pass into ``Lise Bolkónskaya``, a join no single pass
makes. The merged names are now the record's for every guard
(``NameRecord.merged``), before and after the commit (the commit stores them:
``test_node_embedding_text``).
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.graph.resolution.plan import plan_merges
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    MERGED_NAMES_KEY,
    fold,
    name_record,
)
from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor
from tests.fakes.domain_tables import domain_conventions


@pytest.mark.parametrize(
    ("text", "folded"),
    [
        ("Наташа Ростова", "наташа ростова"),
        ("毛泽东", "毛泽东"),
        ("Søren Kierkegaard", "soren kierkegaard"),
        ("Łukasz Nowak", "lukasz nowak"),
        ("Anne-Marie Dupont", "anne-marie dupont"),
        ("sister\u2011in\u2011law", "sister-in-law"),
        ("Natásha's mother", "natasha mother"),
        ("O'Brien", "obrien"),
    ],
)
def test_the_fold_keeps_every_scripts_letters_and_hyphenated_words_whole(
    text: str, folded: str
) -> None:
    """Any script's letters are words; stroked letters fold to their base; a hyphenated word is one."""
    assert fold(text) == folded


def test_a_merge_records_the_names_it_folds_in_first_seen_first() -> None:
    """``merged_names`` grows by every folded-in name, a one-word bare title too.

    The bare title is still no alias (too generic for alias matching), but since
    2026-10-07 (keep, flag, never delete) it is recorded as a name the entity was
    written by: until then ``Emperor`` folded into ``Emperor Alexander`` was kept only
    in ``merged_property_history.aliases_skipped``.
    """
    processor = EntityProcessor(name_conventions=domain_conventions("literary"))
    first = processor.merge_entities(
        {"name": "Princess Bolkónskaya", "type": "Character", "aliases": []},
        {"name": "Princess Mary Bolkónskaya", "type": "Character", "aliases": ["Mary"]},
    )
    second = processor.merge_entities(first, {"name": "Princess", "type": "Character"})
    third = processor.merge_entities(
        second,
        {"name": "Masha", "type": "Character", MERGED_NAMES_KEY: ["Márya Bolkónskaya"]},
    )
    assert first[MERGED_NAMES_KEY] == ["Princess Mary Bolkónskaya"]
    assert second[MERGED_NAMES_KEY] == ["Princess Mary Bolkónskaya", "Princess"]
    assert "Princess" not in second["aliases"]  # a bare title is a merged name, never an alias
    assert third[MERGED_NAMES_KEY] == [
        "Princess Mary Bolkónskaya", "Princess", "Masha", "Márya Bolkónskaya",
    ]  # fmt: skip
    assert name_record(third).merged == tuple(third[MERGED_NAMES_KEY])


def _node(nid: str, label: str, **props: Any) -> dict[str, Any]:
    """A committed node in the planner's shape."""
    return {"id": nid, "label": label, "type": "Character", "aliases": [], "properties": props}


def test_the_resolver_keeps_a_node_apart_from_a_name_one_of_its_merged_names_is_apart_from() -> (
    None
):
    """The committed node carries its merged names; the planner guards it by them."""
    conventions = domain_conventions("literary")
    merged = _node("a", "Princess Bolkónskaya", **{MERGED_NAMES_KEY: ["Princess Mary Bolkónskaya"]})
    plain = _node("a", "Princess Bolkónskaya")
    lise = _node("b", "Lise Bolkónskaya")
    assert [(m.keep, m.drop) for m in plan_merges([plain, lise], [], conventions=conventions)] == [
        ("b", "a")
    ]
    assert plan_merges([merged, lise], [], conventions=conventions) == []
