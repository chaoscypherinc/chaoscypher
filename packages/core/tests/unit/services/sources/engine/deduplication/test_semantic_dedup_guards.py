# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Semantic deduplication passes the name rule's guards before any bonus or merge (audit W4-3).

The semantic pass added 0.10 when two entities shared an alias and 0.15 when
one's name was among the other's aliases, with no gender, generation,
descriptor, holder or group check: with fixed cosines it merged Anatole into
Prince Vasíli Kurágin (0.86, the shared alias ``Kurágin``), Count Rostóv into
Countess Rostóva (0.86, ``Rostóv``) and Princess Hélène into Prince
Hippolyte (0.81, his name among her aliases). On v3 those pairs stayed apart
only because the dedup embedder's cosine fell under the bar. Now every
candidate pair (and a running cluster against each candidate) passes
``NameIndex.groups_apart``, the exact step's guard, and a name in the other's
aliases earns its bonus only as ``alias_evidence``. Run through
``run_deduplication`` with fixed embeddings, under both arms of the A/B.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
    plan_filtering_config,
)
from chaoscypher_core.services.sources.engine.extraction.extractor import run_deduplication
from chaoscypher_core.settings import EngineSettings


def _entity(name: str, *aliases: str) -> dict[str, Any]:
    """A Character mention."""
    return {"name": name, "type": "Character", "aliases": list(aliases)}


def _pairwise_vectors(cosines: list[float]) -> list[list[float]]:
    """Two unit vectors per pair with the pair's cosine; every other pair orthogonal to it."""
    dims = 2 * len(cosines)
    out: list[list[float]] = []
    for k, cos in enumerate(cosines):
        a = [0.0] * dims
        b = [0.0] * dims
        a[2 * k] = 1.0
        b[2 * k] = cos
        b[2 * k + 1] = math.sqrt(1 - cos * cos)
        out.extend([a, b])
    return out


async def _merged(
    pairs: list[tuple[dict[str, Any], dict[str, Any], float]], domain: str
) -> list[bool]:
    """For each pair, whether ``run_deduplication`` (semantic mode, balanced preset) made it one entity."""
    entities: list[dict[str, Any]] = []
    for i, (a, b, _cos) in enumerate(pairs):
        entities.extend(
            [{**a, "source_chunk_indices": [2 * i]}, {**b, "source_chunk_indices": [2 * i + 1]}]
        )
    settings = EngineSettings()
    assert settings.source_processing.entity_deduplication_mode == "semantic"
    out, _rels, _emb, _log = await run_deduplication(
        entities=entities,
        relationships=[],
        detected_domain=domain,
        settings=settings,
        embedding_service=object(),
        filtering_config=plan_filtering_config("balanced", None),
        precomputed_embeddings=_pairwise_vectors([cos for _a, _b, cos in pairs]),
    )
    groups = [set(e["source_chunk_indices"]) for e in out]
    return [any({2 * i, 2 * i + 1} <= g for g in groups) for i in range(len(pairs))]


AUDIT_PAIRS = [
    (_entity("Anatole", "Kurágin"), _entity("Prince Vasíli Kurágin", "Kurágin"), 0.86),
    (_entity("Count Rostóv", "Rostóv"), _entity("Countess Rostóva", "Rostóv"), 0.86),
    (_entity("Princess Hélène", "Prince Hippolyte"), _entity("Prince Hippolyte"), 0.81),
]
"""The three pairs the audit's fixed cosines merged."""


@pytest.mark.asyncio
async def test_the_audits_pairs_stay_apart_under_the_literary_domain() -> None:
    """The cosines that merged them, and a near-identical vector too: the names say two people."""
    assert await _merged(AUDIT_PAIRS, "literary") == [False, False, False]
    near = [(a, b, 0.995) for a, b, _cos in AUDIT_PAIRS]
    assert await _merged(near, "literary") == [False, False, False]


@pytest.mark.asyncio
async def test_a_shared_alias_earns_no_merge_under_the_generic_domain() -> None:
    """Without the literary tables a shared surname still earns nothing (alias-to-alias is no evidence).

    The third pair's alias is evidence under the generic tables (no title says
    a gender there), so the semantic pass follows the exact step: the alias
    key joins them before any vector is read.
    """
    merged = await _merged(AUDIT_PAIRS, "generic")
    assert merged[:2] == [False, False]
    exact_only = await _merged([(a, b, 0.0) for a, b, _cos in AUDIT_PAIRS[2:]], "generic")
    assert merged[2] == exact_only[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["literary", "generic"])
async def test_one_person_spelled_two_ways_still_merges(domain: str) -> None:
    """No clash between the names: the vectors decide, as before (no name bonus: the bar is 0.90 + 0.08)."""
    pair = [(_entity("Napoleon Bonaparte"), _entity("Napoleon Buonaparte", "Bonaparte"), 0.99)]
    assert await _merged(pair, domain) == [True]


@pytest.mark.asyncio
async def test_a_cluster_never_takes_a_member_the_names_keep_apart_from_another() -> None:
    """The running cluster is checked, not the first member alone: Mary joins, then Lise does not."""
    vectors = [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]
    entities = [
        {"name": "Princess Bolkónskaya", "type": "Character", "aliases": [], "source_chunk_indices": [0]},
        {"name": "Princess Mary Bolkónskaya", "type": "Character", "aliases": [], "source_chunk_indices": [1]},
        {"name": "Lise Bolkónskaya", "type": "Character", "aliases": [], "source_chunk_indices": [2]},
    ]  # fmt: skip
    out, _r, _e, _l = await run_deduplication(
        entities=entities,
        relationships=[],
        detected_domain="literary",
        settings=EngineSettings(),
        embedding_service=object(),
        filtering_config=plan_filtering_config("balanced", None),
        precomputed_embeddings=vectors,
    )
    groups = sorted(sorted(e["source_chunk_indices"]) for e in out)
    assert [1, 2] not in groups and [0, 1, 2] not in groups
