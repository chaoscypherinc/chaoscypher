# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Audit wave 5: a woman's title on a lone word, mention order, and the one merge predicate.

Every grouping runs through both real callers (``run_deduplication`` and the
resolver's ``plan_merges``) under both arms of the domain A/B.

* A woman's title on a lone word fits a name that does not say a woman only
  where the names show the word to be a woman's given name: a non-final
  word of a woman's label of two or more words, never a final word, and
  never the one-word fallback (W4-2's regression: ``Miss Bingley`` joined
  ``Bingley``, ``Mrs Henry`` joined ``Henry Crawford``).
* The exact dedup groups identical names before alias evidence, and merged
  names count as holders, so the order of the mentions never decides a
  grouping (the E2 trap: the second ``Princess Bolkónskaya`` joined Princess
  Mary in one order and the little princess in the other).
* Semantic dedup asks the same predicate as the exact steps
  (``NameIndex.allows_merge``): at a cosine of 1.0 a father does not merge
  into his son.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import sqlite3
from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    NameIndex,
    NameRecord,
)
from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor
from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
    DomainResolver,
    domain_name_conventions,
)
from chaoscypher_core.services.sources.engine.extraction.extractor import run_deduplication
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.repo_files import repo_root
from tests.unit.services.sources.engine.deduplication.test_name_rule_holders import (
    BOTH,
    Spec,
    _canon,
    _dedup_groups,
    _resolver_groups,
)


APART_IN_BOTH_ARMS: dict[str, Spec] = {
    "Miss Bingley is not Bingley": [("Miss Bingley", []), ("Bingley", [])],
    "Mrs Bennet is not Bennet": [("Mrs Bennet", []), ("Bennet", [])],
    "Mrs Bennet is not Bennet beside Mr Bennet": [
        ("Mrs Bennet", []),
        ("Mr Bennet", []),
        ("Bennet", []),
    ],
    "Mrs Grant is not Dr Grant": [("Mrs Grant", []), ("Dr Grant", [])],
    "Mrs Martin is not Martin Luther": [("Mrs Martin", []), ("Martin Luther", [])],
    "Mrs Henry is not Henry Crawford": [("Mrs Henry", []), ("Henry Crawford", [])],
    "Lady Russell is not Russell Crowe": [("Lady Russell", []), ("Russell Crowe", [])],
    "Countess Thomas is not Thomas Rostóv": [("Countess Thomas", []), ("Thomas Rostóv", [])],
    "Mrs Henry is not Henry beside Henry Crawford": [
        ("Mrs Henry", []),
        ("Henry", []),
        ("Henry Crawford", []),
    ],
}
"""The wave-5 traps: a woman's title on a lone word that nothing shows to be her given name.

In the last, ``Henry`` could be Mrs Henry's family name or Henry Crawford's
given name: two candidates, so it joins neither."""


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
@pytest.mark.parametrize("case", list(APART_IN_BOTH_ARMS))
async def test_a_womans_title_on_a_lone_word_stays_apart(case: str, domain: str) -> None:
    """Every record its own group, through the deduplication and the resolver."""
    spec = APART_IN_BOTH_ARMS[case]
    alone = _canon([[name] for name, _ in spec])
    assert await _dedup_groups(spec, domain) == alone
    assert _resolver_groups(spec, domain) == alone


@pytest.mark.parametrize(
    ("titled", "word", "evidence"),
    [
        ("Princess Mary", "Mary", "Princess Mary Bolkónskaya"),
        ("Countess Natásha", "Natásha", "Natásha Rostóva"),
        ("Countess Véra", "Véra", "Véra Rostóva"),
    ],
)
def test_a_womans_given_name_shown_elsewhere_lets_the_title_fit(
    titled: str, word: str, evidence: str
) -> None:
    """W4-2 still joins where a woman's fuller name shows the given name (literary)."""
    conventions = domain_name_conventions("literary", DomainResolver(EngineSettings()))
    records = [NameRecord(titled, "Character"), NameRecord(word, "Character")]
    alone = NameIndex(records, conventions=conventions)
    assert alone.pair(0, 1) is None  # the one-word fallback is no evidence
    shown = NameIndex([*records, NameRecord(evidence, "Character")], conventions=conventions)
    assert shown.pair(0, 1) is not None
    # ... and a final word anywhere says the word is a family name
    named_after = NameIndex(
        [*records, NameRecord(evidence, "Character"), NameRecord(f"Ivan {word}", "Character")],
        conventions=conventions,
    )
    assert named_after.pair(0, 1) is None


# -- mention order --------------------------------------------------------------------------

TRAP: list[tuple[str, tuple[str, ...]]] = [
    ("Lise", ()),
    ("Princess Bolkónskaya", ("Lise",)),
    ("Princess Bolkónskaya", ()),
    ("Princess Mary Bolkónskaya", ()),
]
"""The E2 trap: the little princess is Lise; Princess Mary is someone else."""


async def _groups_in_order(
    records: list[tuple[str, tuple[str, ...]]], order: list[int], domain: str
) -> frozenset[frozenset[int]]:
    """The grouping ``run_deduplication`` makes of ``records`` given in ``order``, by record index."""
    work = [
        {
            "name": records[k][0],
            "type": "Character",
            "aliases": list(records[k][1]),
            "source_chunk_indices": [k],
        }
        for k in order
    ]
    out, *_ = await run_deduplication(
        entities=work,
        relationships=[],
        detected_domain=domain,
        settings=EngineSettings(),
        embedding_service=None,
    )
    return frozenset(frozenset(o["source_chunk_indices"]) for o in out)


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
async def test_every_order_of_the_trap_gives_one_grouping(domain: str) -> None:
    """All 24 orders: the little princess is Lise, Princess Mary is alone."""
    seen = {
        await _groups_in_order(TRAP, list(order), domain)
        for order in itertools.permutations(range(len(TRAP)))
    }
    assert seen == {frozenset({frozenset({0, 1, 2}), frozenset({3})})}


_V3_PACK = repo_root() / "internal/benchmarks/reference/war_and_peace_book1-v3/app.db"


def _exploded_v3() -> list[tuple[str, str, tuple[str, ...]]]:
    """v3's entities (read-only) with every alias also a mention of its own, as per-chunk extraction gives them."""
    with sqlite3.connect(f"file:{_V3_PACK}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT name, type, attributes FROM source_entities ORDER BY ordinal"
        ).fetchall()
    out: list[tuple[str, str, tuple[str, ...]]] = []
    for name, kind, attributes in rows:
        aliases = tuple(json.loads(attributes or "{}").get("aliases") or [])
        out.append((name, kind, aliases))
        out.extend((alias, kind, ()) for alias in aliases)
    return out


@pytest.mark.skipif(not _V3_PACK.exists(), reason="the v3 reference pack is not in this tree")
@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
async def test_shuffles_of_the_exploded_v3_give_one_grouping(domain: str) -> None:
    """Ten shuffles of v3's 179 mentions: one grouping (until wave 5, 20 shuffles gave 17 to 20)."""
    records = _exploded_v3()
    seen = set()
    for seed in range(10):
        order = list(range(len(records)))
        random.Random(seed).shuffle(order)
        work = [
            {
                "name": records[k][0],
                "type": records[k][1],
                "aliases": list(records[k][2]),
                "source_chunk_indices": [k],
            }
            for k in order
        ]
        out, *_ = await run_deduplication(
            entities=work,
            relationships=[],
            detected_domain=domain,
            settings=EngineSettings(),
            embedding_service=None,
        )
        seen.add(frozenset(frozenset(o["source_chunk_indices"]) for o in out))
    assert len(seen) == 1


def test_a_name_merged_in_still_holds_its_words() -> None:
    """``Pierre`` that absorbed ``Pierre Bezúkhov`` holds ``Bezúkhov``: the count stays apart."""
    conventions = domain_name_conventions("literary", DomainResolver(EngineSettings()))
    records = [
        NameRecord("Count Bezúkhov", "Character"),
        NameRecord("Pierre", "Character", merged=("Pierre Bezúkhov",)),
        NameRecord("Cyril Vladímirovich Bezúkhov", "Character"),
    ]
    index = NameIndex(records, conventions=conventions)
    assert 1 in index.holders(0, index.words_of(0))
    assert not index.allows_merge([0], [2])
    assert not index.allows_merge([0], [1])


# -- semantic dedup: the one merge predicate ------------------------------------------------

SEMANTIC_CONTEXT = [
    "Prince Andrew Bolkónski",
    "Nicholas Rostóv",
    "Natásha Rostóva",
    "Pierre Bezúkhov",
    "Cyril Vladímirovich Bezúkhov",
    "Prince Vasíli Kurágin",
]
"""Fuller names v3 holds beside the exposed pairs (the holders the guards read)."""

EXPOSED = {
    "literary": [
        ("old Prince Bolkónski", "Prince Andrew"),
        ("Count Rostóv", "Nicholas"),
        ("Countess Rostóva", "Natásha"),
        ("Pierre", "Count Bezúkhov"),
    ],
    "generic": [
        ("Prince Vasíli", "Prince Andrew"),
        ("Pierre", "Count Bezúkhov"),
    ],
}
"""Pairs the group check alone let merge at a cosine of 0.98 (the wave-5 semantic sweep)."""


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", BOTH)
@pytest.mark.parametrize("cos", [0.9, 0.98, 1.0])
async def test_semantic_dedup_never_merges_what_the_names_withhold(domain: str, cos: float) -> None:
    """Every exposed pair stays two entities at any cosine, beside v3's fuller names."""
    conventions = domain_name_conventions(domain, DomainResolver(EngineSettings()))
    processor = EntityProcessor(name_conventions=conventions)
    for a, b in EXPOSED[domain]:
        entities = [
            {"name": n, "type": "Character", "aliases": []} for n in (a, b, *SEMANTIC_CONTEXT)
        ]
        vectors = [
            [1.0, 0.0, *([0.0] * len(SEMANTIC_CONTEXT))],
            [cos, math.sqrt(max(0.0, 1 - cos * cos)), *([0.0] * len(SEMANTIC_CONTEXT))],
            *(
                [0.0, 0.0, *(1.0 if k == m else 0.0 for m in range(len(SEMANTIC_CONTEXT)))]
                for k in range(len(SEMANTIC_CONTEXT))
            ),
        ]
        out, mapping, _ = await processor.deduplicate_entities_semantic(
            entities, None, 0.85, precomputed_embeddings=vectors
        )
        assert mapping[0] != mapping[1], (domain, a, b, cos)


@pytest.mark.asyncio
async def test_semantic_dedup_still_merges_what_the_names_allow() -> None:
    """Two names with nothing against them merge on close vectors (the predicate is no blanket ban).

    Until 2026-10-07 the pair was a person and a bare title (``Lise``, ``the
    little princess``); a reference now joins a name only on its own names
    (``NameIndex.withheld``, below), so the pair is two events.
    """
    processor = EntityProcessor(
        name_conventions=domain_name_conventions("literary", DomainResolver(EngineSettings()))
    )
    entities = [
        {"name": "Anna Pávlovna's reception", "type": "Event", "aliases": []},
        {"name": "the soirée", "type": "Event", "aliases": []},
    ]
    out, mapping, _ = await processor.deduplicate_entities_semantic(
        entities, None, 0.85, precomputed_embeddings=[[1.0, 0.0], [1.0, 0.0]]
    )
    assert mapping[0] == mapping[1]


@pytest.mark.asyncio
async def test_semantic_dedup_never_folds_a_reference_into_a_name_on_vectors_alone() -> None:
    """``the little princess`` is not ``Lise`` for close vectors: its referent comes from its own names.

    Book One has two emperors: an ``Emperor`` description is close to both, so
    a vector is no evidence of whom a bare title is (2026-10-07).
    """
    processor = EntityProcessor(
        name_conventions=domain_name_conventions("literary", DomainResolver(EngineSettings()))
    )
    entities = [
        {"name": "Lise", "type": "Character", "aliases": []},
        {"name": "the little princess", "type": "Character", "aliases": []},
    ]
    out, mapping, _ = await processor.deduplicate_entities_semantic(
        entities, None, 0.85, precomputed_embeddings=[[1.0, 0.0], [1.0, 0.0]]
    )
    assert mapping[0] != mapping[1]


@pytest.mark.asyncio
async def test_the_exact_and_semantic_steps_call_one_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``NameIndex.allows_merge`` turned off: the semantic step merges, the exact step's group check is gone."""
    calls: list[str] = []
    real = NameIndex.allows_merge

    def spy(self: NameIndex, a: Any, b: Any) -> bool:
        calls.append("x")
        return real(self, a, b)

    monkeypatch.setattr(NameIndex, "allows_merge", spy)
    processor = EntityProcessor()
    processor.deduplicate_entities_with_mapping(
        [
            {"name": "Ann", "type": "Character", "aliases": ["Annie"]},
            {"name": "Annie", "type": "Character", "aliases": []},
        ]
    )
    exact_calls = len(calls)
    await processor.deduplicate_entities_semantic(
        [{"name": "Ann", "type": "Character"}, {"name": "Nan", "type": "Character"}],
        None,
        0.85,
        precomputed_embeddings=[[1.0, 0.0], [1.0, 0.0]],
    )
    assert exact_calls >= 1
    assert len(calls) > exact_calls


# -- the descriptor rule reads person-like types only -----------------------------------------


def test_a_possessive_in_a_works_name_is_part_of_the_name() -> None:
    """``Caesar's Commentaries`` (a work) keeps its naming words; ``Natásha's mother`` names no one."""
    from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
        move_relation_descriptors,
    )

    conventions = domain_name_conventions("literary", DomainResolver(EngineSettings()))
    assert conventions.is_person("Character") and conventions.is_person("Author")
    assert not conventions.is_person("Literary Work")
    index = NameIndex(
        [
            NameRecord("Caesar's Commentaries", "Literary Work"),
            NameRecord("Commentaries", "Literary Work"),
            NameRecord("Natásha's mother", "Character"),
        ],
        conventions=conventions,
    )
    assert index.words_of(0) == frozenset({"caesar", "commentaries"})
    assert index.words_of(2) == frozenset()
    assert index.pair(0, 1) is not None
    entities = [
        {"name": "Commentaries", "type": "Literary Work", "aliases": ["Caesar's Commentaries"]},
        {"name": "Countess Rostóva", "type": "Character", "aliases": ["Natásha's mother"]},
    ]
    move_relation_descriptors(entities, conventions)
    assert entities[0]["aliases"] == ["Caesar's Commentaries"]
    assert entities[1]["descriptors"] == ["Natásha's mother"]
