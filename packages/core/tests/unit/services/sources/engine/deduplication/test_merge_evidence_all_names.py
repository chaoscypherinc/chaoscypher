# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Merge evidence is read over all of a record's names, never its label alone (audit wave 7).

W7-4: since wave 6 the survivor of ``Cyril Vladímirovich Bezúkhov``'s group is
his untitled name, and ``_title_fit`` read only the label: the count's title
no longer fitted, the family check counted Pierre (who bears the name too),
and ``Count Bezúkhov`` stopped joining him on the exploded v3 set (10 of 10
shuffles at the parent, 0 of 10 at HEAD). Now the word steps compare every
name of each record (label, given aliases, merged-in names), each carrying
what all of them state (title, gender, generation), so which spelling a merge
left as the label never decides a later join.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.deduplication import name_identity as ni
from chaoscypher_core.services.sources.engine.extraction.extractor import run_deduplication
from chaoscypher_core.settings import EngineSettings
from tests.fakes.domain_tables import domain_conventions


CVB = "Cyril Vladímirovich Bezúkhov"
COUNT_CVB = "Count Cyril Vladímirovich Bezúkhov"

OWN_SPELLINGS: dict[str, list[tuple[str, list[str]]]] = {
    "untitled own, titled mention": [(CVB, [COUNT_CVB]), (COUNT_CVB, [])],
    "titled own, untitled mention": [(COUNT_CVB, [CVB]), (CVB, [])],
    "untitled own, titled alias only": [(CVB, [COUNT_CVB])],
    "titled own, untitled alias only": [(COUNT_CVB, [CVB])],
}
"""Which of the count's two spellings is the entity's own, and whether the other is a mention."""

PIERRE_STATING_MONSIEUR = [
    ("Pierre", ["Monsieur Pierre", "Pierre Bezúkhov"]),
    ("Monsieur Pierre", []),
    ("Pierre Bezúkhov", []),
]
"""Pierre's mentions as v3 has them: ``Monsieur`` says he is no count."""


async def _grouping(
    spec: list[tuple[str, list[str]]], domain: str, seed: int
) -> set[frozenset[str]]:
    """The deduplication's groups (as their mention names) over ``spec`` in a shuffled order."""
    order = list(range(len(spec)))
    if seed:
        random.Random(seed).shuffle(order)
    entities: list[dict[str, Any]] = [
        {"name": spec[k][0], "type": "Character", "aliases": list(spec[k][1]),
         "source_chunk_indices": [k]}
        for k in order
    ]  # fmt: skip
    out, _r, _e, _l = await run_deduplication(
        entities=entities,
        relationships=[],
        detected_domain=domain,
        settings=EngineSettings(),
        embedding_service=None,
    )
    return {frozenset(spec[i][0] for i in e["source_chunk_indices"]) for e in out}


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["literary", "generic"])
async def test_the_count_joins_his_full_name_whichever_spelling_is_his_own(domain: str) -> None:
    """One grouping over every own-spelling variant and ten input orders: the count is Cyril."""
    found: set[frozenset[str]] = set()
    for variant, cyril in OWN_SPELLINGS.items():
        spec = [("Count Bezúkhov", []), *cyril, *PIERRE_STATING_MONSIEUR]
        for seed in range(10):
            groups = await _grouping(spec, domain, seed)
            count = next(g for g in groups if "Count Bezúkhov" in g)
            assert {"Count Bezúkhov", *(name for name, _ in cyril)} == set(count), (variant, seed)
            found.add(frozenset(groups - {count}))
    assert len(found) == 1  # Pierre's group the same in every variant


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["literary", "generic"])
async def test_an_unmarked_bearer_of_the_name_keeps_the_count_apart_in_every_variant(
    domain: str,
) -> None:
    """Beside a plain ``Pierre Bezúkhov`` the count could be either: apart, whatever the label."""
    for variant, cyril in OWN_SPELLINGS.items():
        spec = [("Count Bezúkhov", []), *cyril, ("Pierre Bezúkhov", [])]
        for seed in range(5):
            groups = await _grouping(spec, domain, seed)
            assert frozenset({"Count Bezúkhov"}) in groups, (variant, seed)


def test_the_resolver_reads_every_name_too() -> None:
    """The planner (``plan_name_pairs`` over committed nodes): a merged-in titled name fits."""
    records = [
        ni.NameRecord("Count Bezúkhov", "Character"),
        ni.NameRecord(CVB, "Character", merged=(COUNT_CVB,)),
        ni.NameRecord("Pierre", "Character", merged=("Monsieur Pierre", "Pierre Bezúkhov")),
    ]
    for domain in ("literary", "generic"):
        pairs = ni.plan_name_pairs(records, conventions=domain_conventions(domain))
        assert [(i, j) for i, j, _ in pairs] == [(0, 1)], domain
        # the label alone (the wave-6 reading) withholds it
        index = ni.NameIndex(records, conventions=domain_conventions(domain))
        assert len(index.views(1)) == 2
        assert index.views(1)[0].title_gender == "m"  # the label carries the group's title
