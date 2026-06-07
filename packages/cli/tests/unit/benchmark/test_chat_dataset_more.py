# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage for GraphRAGChatDataset out-of-scope judging, outer failure, and parsers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_cli.benchmark.chat_dataset import (
    GraphRAGChatDataset,
    _format_retrieved,
    _parse_int_1_5,
    _parse_yes_no,
)
from chaoscypher_cli.benchmark.models import ModelConfig
from chaoscypher_cli.benchmark.queries import LabeledQuery, LabeledQuerySet


def _chat_model() -> ModelConfig:
    return ModelConfig(provider="ollama", model="llama", label="L")


def _judge() -> ModelConfig:
    return ModelConfig(provider="anthropic", model="claude-opus-4-7", label="J")


def _oos_qs() -> LabeledQuerySet:
    return LabeledQuerySet(
        version="1.0",
        queries=[
            LabeledQuery(
                id="q1", band="out_of_scope", question="unanswerable?", expect_refusal=True
            )
        ],
    )


@pytest.mark.asyncio
async def test_out_of_scope_query_scores_refusal() -> None:
    """An out_of_scope query routes through the REFUSAL_PROMPT judge branch."""
    indexed = MagicMock()

    @asynccontextmanager
    async def fake_indexed_graph():
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph

    graphrag_search = AsyncMock(
        return_value={"entities": [{"id": "e1", "name": "Topic", "description": "d"}]}
    )
    chat_call = AsyncMock(return_value="I cannot answer from the corpus.")
    # First judge call = faithfulness (returns "4"), second = refusal ("yes").
    judge_call = AsyncMock(side_effect=["4", "yes"])

    ds = GraphRAGChatDataset(
        id="demo",
        version="1.0",
        corpus_id="demo",
        queries=_oos_qs(),
        graph_provider=provider,
        graphrag_search=graphrag_search,
        chat=chat_call,
        judge=_judge(),
        judge_call=judge_call,
    )
    out = await ds.run(_chat_model())

    assert out.error is None
    scores = out.extras["per_query"][0]["judge_scores"]
    assert scores["faithfulness"] == 4
    assert scores["correctness"] is None  # not scored for out_of_scope
    assert scores["refusal_correct"] is True
    assert judge_call.await_count == 2


@pytest.mark.asyncio
async def test_outer_failure_when_indexed_graph_raises() -> None:
    """If indexed_graph() raises, run returns an error RawOutput (outer except)."""

    @asynccontextmanager
    async def boom_indexed_graph():
        raise RuntimeError("graph load failed")
        yield  # pragma: no cover - unreachable, makes it an async generator

    provider = MagicMock()
    provider.indexed_graph = boom_indexed_graph

    ds = GraphRAGChatDataset(
        id="demo",
        version="1.0",
        corpus_id="demo",
        queries=_oos_qs(),
        graph_provider=provider,
        graphrag_search=AsyncMock(),
        chat=AsyncMock(),
        judge=_judge(),
        judge_call=AsyncMock(),
    )
    out = await ds.run(_chat_model())

    assert out.error is not None
    assert "RuntimeError" in out.error
    assert "graph load failed" in out.error
    # Outer failure still carries judge metadata + the (empty) per_query list.
    assert out.extras["judge_provider"] == "anthropic"
    assert out.extras["per_query"] == []


def test_parse_int_1_5_finds_first_valid() -> None:
    assert _parse_int_1_5("The score is 4 out of 5") == 4


def test_parse_int_1_5_returns_none_when_out_of_range() -> None:
    # 9 is a digit but out of the 1-5 range -> skipped; no valid token -> None.
    assert _parse_int_1_5("score: 9") is None


def test_parse_int_1_5_returns_none_without_digits() -> None:
    assert _parse_int_1_5("no number here") is None


def test_parse_yes_no_variants() -> None:
    assert _parse_yes_no("Yes, it refused correctly") is True
    assert _parse_yes_no("NO it did not") is False
    assert _parse_yes_no("maybe") is None


def test_format_retrieved_empty_placeholder() -> None:
    assert _format_retrieved({"entities": []}) == "(empty)"


def test_format_retrieved_with_and_without_description() -> None:
    text = _format_retrieved(
        {
            "entities": [
                {"name": "Alpha", "description": "first"},
                {"name": "Beta"},
            ]
        }
    )
    assert "- Alpha: first" in text
    assert "- Beta" in text
