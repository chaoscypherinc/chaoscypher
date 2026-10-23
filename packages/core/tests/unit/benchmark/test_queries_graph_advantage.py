# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""queries.yaml: the ``graph_advantage`` band, its ``kind`` field and the ``candidate`` flag."""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.benchmark.queries import BAND_VALUES, KINDS, LabeledQuerySet, load_queries


_HEAD = 'version: "1.0"\nqueries:\n'

_BAND_Q = """  - id: q081
    band: graph_advantage
    kind: relationship_path
    candidate: true
    question: "How is Borís connected to Count Bezúkhov?"
    gold_entities: ["Borís", "Count Bezúkhov"]
    answer_terms: ["godfather"]
    gold_answer: "The count is his godfather."
"""

_PLAIN_Q = """  - id: q001
    band: factual_single_hop
    question: "Who built the IMP?"
    gold_entities: ["BBN"]
    gold_answer: "BBN."
"""


def _load(tmp_path: Path, body: str) -> LabeledQuerySet:
    path = tmp_path / "queries.yaml"
    path.write_text(_HEAD + body, encoding="utf-8")
    return load_queries(path)


def test_the_band_and_its_four_kinds_are_known() -> None:
    assert "graph_advantage" in BAND_VALUES
    assert KINDS == ("cross_chapter_join", "relationship_path", "cast_aggregate", "disambiguation")


def test_a_band_question_parses_its_kind_and_candidate_flag(tmp_path: Path) -> None:
    """``kind`` is read on the band; ``candidate`` is read and defaults to False elsewhere."""
    qs = _load(tmp_path, _PLAIN_Q + _BAND_Q)
    plain, band = qs.queries
    assert (plain.kind, plain.candidate) == (None, False)
    assert band.band == "graph_advantage"
    assert band.kind == "relationship_path" and band.candidate is True
    assert band.answerable is True


def test_a_band_question_without_a_kind_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="kind"):
        _load(tmp_path, _BAND_Q.replace("    kind: relationship_path\n", ""))


def test_an_unknown_kind_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="kind"):
        _load(tmp_path, _BAND_Q.replace("relationship_path", "trivia"))


def test_a_kind_off_the_band_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="kind"):
        _load(
            tmp_path,
            _PLAIN_Q.replace(
                '    question: "Who built the IMP?"\n',
                '    kind: disambiguation\n    question: "Who built the IMP?"\n',
            ),
        )


def test_answerable_false_is_rejected_on_the_band(tmp_path: Path) -> None:
    """A rung's retrieval miss is the measurement on this band, never an exclusion."""
    with pytest.raises(ValueError, match="answerable"):
        _load(tmp_path, _BAND_Q + "    answerable: false\n")
