# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The Book One ``graph_advantage`` band: the retrieval comparison's questions."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from chaoscypher_cli.benchmark.discovery import builtin_dataset_root
from chaoscypher_core.benchmark.queries import KINDS, load_queries
from chaoscypher_core.benchmark.scorers.chat_probes import BAND_TIER, ChatProbeScorer


BUILTIN = builtin_dataset_root() / "war_and_peace_book1" / "queries.yaml"
PACK = (
    Path(__file__).resolve().parents[5]
    / "internal"
    / "benchmarks"
    / "reference"
    / "war_and_peace_book1-v2"
    / "queries.yaml"
)
PUBLISHED = 40
PER_KIND = 10


def _band() -> list:
    return [q for q in load_queries(BUILTIN).queries if q.band == "graph_advantage"]


def test_the_band_is_the_published_forty_ten_per_kind_no_candidates_left() -> None:
    """The keep rule was applied 2026-10-02: forty questions, ten of each kind, none still a candidate.

    Ids keep their writing order but are not contiguous: five were dropped
    as unreachable by every rung on the v2 pack and eleven set aside before any model ran.
    """
    band = _band()
    assert len(band) == PUBLISHED
    per_kind = Counter(q.kind for q in band)
    assert set(per_kind) == set(KINDS)
    assert all(n == PER_KIND for n in per_kind.values()), per_kind
    assert not any(q.candidate for q in band)
    ids = [q.id for q in band]
    assert ids == sorted(ids) and all(int(i[1:]) > 80 for i in ids)


def test_every_band_question_is_fully_labelled() -> None:
    """A gold answer, answer terms and gold entities on each; the wrong-join list never echoes the question."""
    for q in _band():
        assert q.gold_answer and q.answer_terms and q.gold_entities, q.id
        assert q.answerable is True, q.id
        for term in q.must_not_contain:
            assert term.lower() not in q.question.lower(), (q.id, term)


def test_the_band_is_scored_hard_by_the_in_scope_checks() -> None:
    assert BAND_TIER["graph_advantage"] == "hard"
    assert ChatProbeScorer.CHECKS_BY_BAND["graph_advantage"] == ChatProbeScorer._IN_SCOPE


def test_the_pack_copy_carries_the_band() -> None:
    """The reference pack's fixture is the builtin one, band included (private tree only)."""
    if not PACK.exists():  # the public export strips internal/
        return
    assert PACK.read_bytes() == BUILTIN.read_bytes()
    assert sum(q.band == "graph_advantage" for q in load_queries(PACK).queries) == PUBLISHED
