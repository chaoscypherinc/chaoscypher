# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for queries.yaml parser."""

from __future__ import annotations

import pytest

from chaoscypher_cli.benchmark.queries import (
    BAND_VALUES,
    load_queries,
)


def test_load_in_scope_query(tmp_path):
    p = tmp_path / "queries.yaml"
    p.write_text(
        "version: '1.0'\n"
        "queries:\n"
        "  - id: q001\n"
        "    band: factual_single_hop\n"
        "    question: Who funded ARPANET?\n"
        "    gold_entities: [ARPA, ARPANET]\n"
        "    gold_answer: ARPA funded ARPANET.\n",
        encoding="utf-8",
    )
    qs = load_queries(p)
    assert qs.version == "1.0"
    assert len(qs.queries) == 1
    q = qs.queries[0]
    assert q.id == "q001"
    assert q.band == "factual_single_hop"
    assert q.gold_entities == ["ARPA", "ARPANET"]
    assert q.gold_answer == "ARPA funded ARPANET."
    assert q.expect_refusal is False


def test_load_out_of_scope_query(tmp_path):
    p = tmp_path / "queries.yaml"
    p.write_text(
        "version: '1.0'\n"
        "queries:\n"
        "  - id: q002\n"
        "    band: out_of_scope\n"
        "    question: Unanswerable from corpus?\n"
        "    expect_refusal: true\n",
        encoding="utf-8",
    )
    qs = load_queries(p)
    q = qs.queries[0]
    assert q.expect_refusal is True
    assert q.gold_entities == []
    assert q.gold_answer is None


def test_unknown_band_raises(tmp_path):
    p = tmp_path / "queries.yaml"
    p.write_text(
        "version: '1.0'\nqueries:\n  - {id: q1, band: nonsense, question: 'q?'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown band"):
        load_queries(p)


def test_in_scope_requires_gold(tmp_path):
    p = tmp_path / "queries.yaml"
    p.write_text(
        "version: '1.0'\nqueries:\n  - {id: q1, band: factual_single_hop, question: 'q?'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="gold_entities"):
        load_queries(p)


def test_out_of_scope_rejects_gold(tmp_path):
    p = tmp_path / "queries.yaml"
    p.write_text(
        "version: '1.0'\n"
        "queries:\n"
        "  - id: q1\n"
        "    band: out_of_scope\n"
        "    question: q?\n"
        "    gold_entities: [X]\n"
        "    expect_refusal: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="out_of_scope.*gold_entities"):
        load_queries(p)


def test_band_values_match_literal():
    from typing import get_args

    from chaoscypher_cli.benchmark.queries import Band

    expected = set(get_args(Band))
    assert expected == BAND_VALUES
    assert len(BAND_VALUES) == 5


def test_version_coerces_to_string(tmp_path):
    p = tmp_path / "queries.yaml"
    p.write_text(
        "version: 1\n"
        "queries:\n"
        "  - id: q1\n"
        "    band: out_of_scope\n"
        "    question: q\n"
        "    expect_refusal: true\n",
        encoding="utf-8",
    )
    qs = load_queries(p)
    assert qs.version == "1"


def test_empty_queries_list_rejected(tmp_path):
    p = tmp_path / "queries.yaml"
    p.write_text("version: '1.0'\nqueries: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty"):
        load_queries(p)


def test_in_scope_requires_gold_answer(tmp_path):
    p = tmp_path / "queries.yaml"
    p.write_text(
        "version: '1.0'\n"
        "queries:\n"
        "  - id: q1\n"
        "    band: factual_single_hop\n"
        "    question: q\n"
        "    gold_entities: [X]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="gold_answer"):
        load_queries(p)
