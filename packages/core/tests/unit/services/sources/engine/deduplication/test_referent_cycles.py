# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A reference's referent terminates on any input (2026-10-08, the v5b literary extraction).

The literary arm of War and Peace Book One wrote two references given one
name: ``a girl of thirteen`` and ``the younger girl``, both given
``Natásha``. Whether ``Natásha`` given to one is evidence asked whether every
other holder of the name is that record; the other reference held it, and a
pair of references is read by their referents: each resolution asked the
other's, with no end, and the extraction failed at finalization with a
``RecursionError``. A reference holding a name only as its own alias is no
second holder (both are Natásha); a resolution that still needs itself is a
cycle and resolves to no one; a referent is always a named record.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.deduplication import name_identity as ni
from chaoscypher_core.services.sources.engine.extraction.extractor import run_deduplication
from chaoscypher_core.settings import EngineSettings


_V5B = [
    ("a girl of thirteen", ("Natásha",)),
    ("the younger girl", ("Natásha",)),
    ("Natásha", ()),
]
"""The cycling records of the v5b literary extraction (dedup input records 435, 483 and 442)."""


def _index(spec: list[tuple[str, tuple[str, ...]]]) -> ni.NameIndex:
    return ni.NameIndex([ni.NameRecord(name, "Person", aliases) for name, aliases in spec])


@pytest.mark.parametrize("order", [(0, 1, 2), (1, 0, 2), (2, 1, 0)])
def test_two_references_given_one_name_are_both_that_name(order: tuple[int, ...]) -> None:
    """The v5b pair: neither is a second holder of the other's alias, read in any order."""
    index = _index(_V5B)
    assert [index.reference(i) for i in range(3)] == ["girl thirteen", "younger girl", None]
    assert {i: index.referent(i) for i in order} == {0: 2, 1: 2, 2: None}


def test_a_reference_given_a_fuller_name_is_no_holder_of_the_short_one() -> None:
    """The subset path: ``the young countess`` given Natásha holds no second Natásha."""
    index = _index(
        [("the countess", ("Natásha Rostóva",)), ("the young countess", ("Natásha",)), _V5B[2]]
    )
    assert [index.referent(i) for i in range(3)] == [2, 2, None]


def test_a_long_run_of_references_written_alike_terminates() -> None:
    """Three hundred mentions of ``the countess`` given one name: each is that name."""
    spec = [("the countess", ("Natásha",))] * 300 + [("Natásha", ())]
    index = _index(spec)
    assert {index.referent(i) for i in reversed(range(300))} == {300}


def test_a_true_cycle_resolves_to_no_one() -> None:
    """A resolution that needs its own referent ends there: no one, every reference on it."""

    class _Cycling(ni.NameIndex):
        def _referent(self, i: int) -> int | None:
            other = 1 - i
            return other if self.referent(other) is None else None

    for first in (0, 1):
        index = _Cycling(
            [ni.NameRecord("the count", "Person"), ni.NameRecord("the count", "Person")]
        )
        assert index.referent(first) is None
        assert index.referent(1 - first) == first  # read after: the cycle is closed


def test_resolution_terminates_and_names_only_named_records_on_any_input() -> None:
    """Seeded random records over shared references and names: a referent is always a named record."""
    references = ["the countess", "the count", "a girl of thirteen", "the younger girl", "the boy"]
    names = ["Natásha", "Natásha Rostóva", "Count Rostóv", "Nicholas", "Véra"]
    rng = random.Random(20261008)
    for _ in range(200):
        spec = [
            (
                rng.choice(references + names),
                tuple(rng.sample(references + names, rng.randint(0, 3))),
            )
            for _ in range(rng.randint(2, 12))
        ]
        index = _index(spec)
        for i in rng.sample(range(len(spec)), len(spec)):
            found = index.referent(i)
            assert found is None or (index.reference(found) is None and found != i), spec


async def _dedup(domain: str) -> list[dict[str, Any]]:
    out, _rels, _emb, _log = await run_deduplication(
        entities=[
            {"name": n, "type": "Person", "aliases": list(a), "source_chunk_indices": [k]}
            for k, (n, a) in enumerate(_V5B)
        ],
        relationships=[],
        detected_domain=domain,
        settings=EngineSettings(),
        embedding_service=None,
    )
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["literary", "generic"])
async def test_the_v5b_records_deduplicate_into_natasha(domain: str) -> None:
    """The finalization that failed: one entity, Natásha, the two references folded in."""
    out = await _dedup(domain)
    assert [e["name"] for e in out] == ["Natásha"]
    assert {"a girl of thirteen", "the younger girl"} <= set(out[0]["aliases"])
    assert not ni.pipeline_flag(out[0].get("properties"), ni.DESCRIPTOR_REFERENCE_KEY)
