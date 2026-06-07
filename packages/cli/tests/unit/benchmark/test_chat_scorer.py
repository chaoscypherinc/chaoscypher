# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import pytest

from chaoscypher_cli.benchmark.dataset import RawOutput
from chaoscypher_cli.benchmark.queries import LabeledQuery, LabeledQuerySet
from chaoscypher_cli.benchmark.scorers.chat import (
    CHAT_SCORER_VERSION,
    GraphRAGChatScorer,
)


def _qs(*queries):
    return LabeledQuerySet(version="1.0", queries=list(queries))


def _raw(per_query, judge_provider="anthropic", judge_model="claude-opus-4-7"):
    return RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras={
            "per_query": per_query,
            "judge_provider": judge_provider,
            "judge_model": judge_model,
        },
    )


def test_perfect_in_scope_score():
    qs = _qs(
        LabeledQuery(
            id="q1", band="factual_single_hop", question="?", gold_entities=["A"], gold_answer="a"
        )
    )
    raw = _raw(
        [
            {
                "query_id": "q1",
                "band": "factual_single_hop",
                "judge_scores": {"faithfulness": 5, "correctness": 5, "refusal_correct": None},
            }
        ]
    )
    score = GraphRAGChatScorer().score(raw, qs)
    assert score.headline_score == pytest.approx(100.0)
    assert score.metrics["faithfulness_avg"] == pytest.approx(5.0)
    assert score.metrics["correctness_avg"] == pytest.approx(5.0)


def test_out_of_scope_refusal_correct():
    qs = _qs(LabeledQuery(id="q1", band="out_of_scope", question="?", expect_refusal=True))
    raw = _raw(
        [
            {
                "query_id": "q1",
                "band": "out_of_scope",
                "judge_scores": {"faithfulness": 5, "correctness": None, "refusal_correct": True},
            }
        ]
    )
    score = GraphRAGChatScorer().score(raw, qs)
    assert score.metrics["refusal_correct_rate"] == pytest.approx(1.0)


def test_mixed_aggregate():
    qs = _qs(
        LabeledQuery(
            id="q1", band="factual_single_hop", question="?", gold_entities=["A"], gold_answer="a"
        ),
        LabeledQuery(id="q2", band="out_of_scope", question="?", expect_refusal=True),
    )
    raw = _raw(
        [
            {
                "query_id": "q1",
                "band": "factual_single_hop",
                "judge_scores": {"faithfulness": 4, "correctness": 3, "refusal_correct": None},
            },
            {
                "query_id": "q2",
                "band": "out_of_scope",
                "judge_scores": {"faithfulness": 5, "correctness": None, "refusal_correct": False},
            },
        ]
    )
    score = GraphRAGChatScorer().score(raw, qs)
    assert score.metrics["faithfulness_avg"] == pytest.approx(4.5)
    assert score.metrics["correctness_avg"] == pytest.approx(3.0)
    assert score.metrics["refusal_correct_rate"] == pytest.approx(0.0)
    assert score.metrics["judge_provider"] == "anthropic"
    assert score.metrics["judge_model"] == "claude-opus-4-7"


def test_scorer_version_pinned():
    assert CHAT_SCORER_VERSION == 1
    assert GraphRAGChatScorer().version == CHAT_SCORER_VERSION
