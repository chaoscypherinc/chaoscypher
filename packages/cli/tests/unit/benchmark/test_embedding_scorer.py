# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import pytest

from chaoscypher_cli.benchmark.dataset import RawOutput
from chaoscypher_cli.benchmark.queries import LabeledQuery, LabeledQuerySet
from chaoscypher_cli.benchmark.scorers.embedding import EmbeddingRetrievalScorer


def _qs(*queries):
    return LabeledQuerySet(version="1.0", queries=list(queries))


def _raw(per_query, top_k=10):
    return RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={"per_query": per_query, "top_k": top_k},
    )


def test_perfect_score_is_100():
    qs = _qs(
        LabeledQuery(
            id="q1",
            band="factual_single_hop",
            question="?",
            gold_entities=["A"],
            gold_answer="a",
        )
    )
    raw = _raw(
        [{"query_id": "q1", "band": "factual_single_hop", "ranks": {"A": 1}, "skipped": False}]
    )
    score = EmbeddingRetrievalScorer().score(raw, qs)
    assert score.headline_score == pytest.approx(100.0)
    assert score.metrics["mrr"] == pytest.approx(1.0)
    assert score.metrics["recall_at_1"] == pytest.approx(1.0)
    assert score.metrics["recall_at_3"] == pytest.approx(1.0)


def test_rank_two_gives_half_mrr():
    qs = _qs(
        LabeledQuery(
            id="q1",
            band="paraphrase",
            question="?",
            gold_entities=["A"],
            gold_answer="a",
        )
    )
    raw = _raw([{"query_id": "q1", "band": "paraphrase", "ranks": {"A": 2}, "skipped": False}])
    score = EmbeddingRetrievalScorer().score(raw, qs)
    assert score.metrics["mrr"] == pytest.approx(0.5)
    assert score.metrics["recall_at_1"] == pytest.approx(0.0)
    assert score.metrics["recall_at_3"] == pytest.approx(1.0)


def test_multi_gold_averages_pair_wise():
    qs = _qs(
        LabeledQuery(
            id="q1",
            band="multi_hop",
            question="?",
            gold_entities=["A", "B"],
            gold_answer="a",
        )
    )
    raw = _raw(
        [{"query_id": "q1", "band": "multi_hop", "ranks": {"A": 1, "B": 5}, "skipped": False}]
    )
    score = EmbeddingRetrievalScorer().score(raw, qs)
    assert score.metrics["mrr"] == pytest.approx(0.6)
    assert score.metrics["recall_at_1"] == pytest.approx(0.5)
    assert score.metrics["recall_at_3"] == pytest.approx(0.5)


def test_skipped_queries_excluded_and_counted():
    qs = _qs(
        LabeledQuery(
            id="q1",
            band="factual_single_hop",
            question="?",
            gold_entities=["A"],
            gold_answer="a",
        ),
        LabeledQuery(
            id="q2",
            band="factual_single_hop",
            question="?",
            gold_entities=["B"],
            gold_answer="b",
        ),
    )
    raw = _raw(
        [
            {
                "query_id": "q1",
                "band": "factual_single_hop",
                "ranks": {"A": 1},
                "skipped": False,
            },
            {
                "query_id": "q2",
                "band": "factual_single_hop",
                "ranks": {},
                "skipped": True,
                "skip_reason": "gold_unresolved",
                "unresolved": ["B"],
            },
        ]
    )
    score = EmbeddingRetrievalScorer().score(raw, qs)
    assert score.metrics["mrr"] == pytest.approx(1.0)
    assert score.metrics["queries_unresolved"] == 1
    assert score.metrics["queries_scored"] == 1
    assert score.metrics["queries_total"] == 2


def test_per_band_breakdown():
    qs = _qs(
        LabeledQuery(
            id="q1",
            band="factual_single_hop",
            question="?",
            gold_entities=["A"],
            gold_answer="a",
        ),
        LabeledQuery(
            id="q2",
            band="paraphrase",
            question="?",
            gold_entities=["B"],
            gold_answer="b",
        ),
    )
    raw = _raw(
        [
            {
                "query_id": "q1",
                "band": "factual_single_hop",
                "ranks": {"A": 1},
                "skipped": False,
            },
            {
                "query_id": "q2",
                "band": "paraphrase",
                "ranks": {"B": 4},
                "skipped": False,
            },
        ]
    )
    score = EmbeddingRetrievalScorer().score(raw, qs)
    assert score.metrics["by_band"]["factual_single_hop"]["mrr"] == pytest.approx(1.0)
    assert score.metrics["by_band"]["paraphrase"]["mrr"] == pytest.approx(0.25)
