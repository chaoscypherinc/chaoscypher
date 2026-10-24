# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""queries.yaml: ``wrong_members`` and ``holders``, the over-inclusion fields (chat scorer v8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.benchmark.queries import load_queries


_BASE = """version: "1.0"
queries:
  - id: q1
    band: graph_advantage
    kind: cast_aggregate
    question: "Which sites did both Kahn and Cerf visit?"
    gold_entities: ["Kahn", "Cerf"]
    answer_terms: ["UCLA", "Stanford"]
    wrong_members: ["BBN", "ARPA"]
    holders: [["Kahn", "Robert Kahn"], "Cerf"]
    gold_answer: "UCLA and Stanford."
  - id: q2
    band: factual_single_hop
    question: "Who built the IMP?"
    gold_entities: ["BBN"]
    gold_answer: "BBN."
"""


def test_wrong_members_and_holders_parse_and_default_empty(tmp_path: Path) -> None:
    """A holder is a name or an alias group; a question without the fields has none."""
    path = tmp_path / "queries.yaml"
    path.write_text(_BASE, encoding="utf-8")
    first, second = load_queries(path).queries
    assert first.wrong_members == ["BBN", "ARPA"]
    assert first.holders == [("Kahn", "Robert Kahn"), ("Cerf",)]
    assert second.wrong_members == [] and second.holders == []


def test_holders_without_wrong_members_are_rejected(tmp_path: Path) -> None:
    """Holders only scope where a wrong member may stand: alone they check nothing."""
    path = tmp_path / "queries.yaml"
    path.write_text(_BASE.replace('    wrong_members: ["BBN", "ARPA"]\n', ""), encoding="utf-8")
    with pytest.raises(ValueError, match="holders"):
        load_queries(path)
