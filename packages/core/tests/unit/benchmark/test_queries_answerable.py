# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""queries.yaml: the ``answerable`` flag the fixture audit sets (chat scorer v4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.benchmark.queries import load_queries


_BASE = """version: "1.0"
queries:
  - id: q1
    band: factual_single_hop
    question: "Who built the IMP?"
    gold_entities: ["BBN"]
    gold_answer: "BBN."
  - id: q2
    band: multi_hop
    question: "Who led BBN, and where?"
    gold_entities: ["BBN"]
    gold_answer: "Beranek, in Cambridge."
    answerable: false
  - id: q3
    band: out_of_scope
    question: "Who bought BBN?"
    expect_refusal: true
    decoy_entities: ["Raytheon"]
"""


def test_answerable_defaults_true_and_parses_false(tmp_path: Path) -> None:
    """Unflagged questions are answerable; ``answerable: false`` is read as given."""
    path = tmp_path / "queries.yaml"
    path.write_text(_BASE, encoding="utf-8")
    qs = load_queries(path)
    assert [q.answerable for q in qs.queries] == [True, False, True]


def test_answerable_false_is_rejected_on_out_of_scope(tmp_path: Path) -> None:
    """An out-of-scope question is unanswerable by design; flagging it is a fixture error."""
    path = tmp_path / "queries.yaml"
    path.write_text(
        _BASE.replace(
            'decoy_entities: ["Raytheon"]', 'decoy_entities: ["Raytheon"]\n    answerable: false'
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="answerable"):
        load_queries(path)
