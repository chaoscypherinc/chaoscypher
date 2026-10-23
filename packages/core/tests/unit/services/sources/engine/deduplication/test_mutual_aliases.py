# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Mutual aliases are evidence, not ambiguity (2026-10-07, the first v4 extraction).

The v4 prompt asks for every name a person is written under, so a pair
usually lists each other's names as aliases. The alias-key guard counted the
named record as a second giver, and the fuller-alias guard read the lowercase
epithet in "prodigal son Anatole" as naming words that point at a third
person: 123 entities in, 123 out. Both guards read the pair itself as
"someone else"; they must not.
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.extraction.extractor import run_deduplication
from chaoscypher_core.settings import EngineSettings


def _ents(spec: list[tuple[str, list[str]]]) -> list[dict[str, Any]]:
    return [
        {"name": n, "type": "Character", "aliases": list(a), "source_chunk_indices": [i]}
        for i, (n, a) in enumerate(spec)
    ]


async def _names(spec: list[tuple[str, list[str]]], domain: str = "literary") -> list[str]:
    out, _rels, _emb, _log = await run_deduplication(
        entities=_ents(spec),
        relationships=[],
        detected_domain=domain,
        settings=EngineSettings(),
        embedding_service=None,
    )
    return sorted(e["name"] for e in out)


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["literary", "generic"])
async def test_a_pair_that_lists_each_others_names_merges(domain: str) -> None:
    """The v4 Anatole pair, as the model wrote it: one entity under either arm."""
    names = await _names(
        [
            ("Anatole", ["Anatole", "prodigal son Anatole", "Anatole Kurágin", "Kurágin"]),
            ("Anatole Kurágin", ["Anatole", "Prince Anatole", "Anatole Kurágin"]),
        ],
        domain,
    )
    assert names == ["Anatole Kurágin"]


@pytest.mark.asyncio
async def test_the_other_v4_pairs_merge_and_the_family_stays_apart() -> None:
    """Hippolyte, Lise and Kutúzov pairs join; father and son sharing "Kurágin" do not."""
    names = await _names(
        [
            ("Hippolyte", ["Prince Hippolyte", "Hippolyte"]),
            ("Prince Hippolyte", ["Hippolyte", "Prince Hippolyte Kurágin"]),
            ("Lise", ["Lise Meinen", "the little princess"]),
            ("Lise Meinen", ["Lise", "Princess Bolkónskaya"]),
            ("Kutúzov", ["Michael Ilariónovich Kutúzov", "Kutúzov"]),
            ("Michael Ilariónovich Kutúzov", ["Kutúzov", "General Kutúzov"]),
            ("Prince Vasíli Kurágin", ["Prince Vasíli", "Vasíli Kurágin", "Kurágin"]),
            ("Anatole Kurágin", ["Anatole", "Kurágin"]),
        ]
    )
    assert names == [
        "Anatole Kurágin",
        "Lise Meinen",
        "Michael Ilariónovich Kutúzov",
        "Prince Hippolyte",
        "Prince Vasíli Kurágin",
    ]


@pytest.mark.asyncio
async def test_a_fuller_alias_of_capitalised_names_still_names_someone_else() -> None:
    """The alias "Lise Meinen" on "Princess Bolkónskaya" still names someone other than Princess Mary (wave 2)."""
    names = await _names(
        [
            ("Princess Bolkónskaya", ["Lise Meinen"]),
            ("Princess Mary Bolkónskaya", ["Princess Mary"]),
        ]
    )
    assert names == ["Princess Bolkónskaya", "Princess Mary Bolkónskaya"]
