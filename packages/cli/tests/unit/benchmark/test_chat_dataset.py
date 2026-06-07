# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_cli.benchmark.chat_dataset import (
    GraphRAGChatDataset,
    JudgeVerdict,
)
from chaoscypher_cli.benchmark.models import ModelConfig
from chaoscypher_cli.benchmark.queries import LabeledQuery, LabeledQuerySet


def _qs():
    return LabeledQuerySet(
        version="1.0",
        queries=[
            LabeledQuery(
                id="q1",
                band="factual_single_hop",
                question="?",
                gold_entities=["A"],
                gold_answer="a",
            ),
            LabeledQuery(id="q2", band="out_of_scope", question="?", expect_refusal=True),
        ],
    )


@pytest.mark.asyncio
async def test_run_invokes_pipeline_and_judge():
    chat = ModelConfig(provider="ollama", model="llama", label="L")
    judge = ModelConfig(provider="anthropic", model="claude-opus-4-7", label="J")

    indexed = MagicMock()

    @asynccontextmanager
    async def fake_indexed_graph():
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph

    graphrag_search = AsyncMock(return_value={"entities": [{"id": "uuid-1", "name": "A"}]})
    chat_call = AsyncMock(return_value="A is the answer.")
    judge_call = AsyncMock(side_effect=["5", "5"])

    ds = GraphRAGChatDataset(
        id="demo",
        version="1.0",
        corpus_id="demo",
        queries=_qs(),
        graph_provider=provider,
        graphrag_search=graphrag_search,
        chat=chat_call,
        judge=judge,
        judge_call=judge_call,
    )
    out = await ds.run(chat)
    assert out.error is None
    per_query = out.extras["per_query"]
    assert len(per_query) == 2
    assert per_query[0]["query_id"] == "q1"
    assert per_query[0]["judge_scores"]["faithfulness"] == 5
    assert per_query[0]["judge_scores"]["correctness"] == 5
    assert per_query[1]["query_id"] == "q2"
    assert out.extras["judge_provider"] == "anthropic"
    assert out.extras["judge_model"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_run_records_chat_failure_per_query():
    chat = ModelConfig(provider="ollama", model="llama", label="L")
    judge = ModelConfig(provider="anthropic", model="claude-opus-4-7", label="J")

    indexed = MagicMock()

    @asynccontextmanager
    async def fake_indexed_graph():
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph

    graphrag_search = AsyncMock(return_value={"entities": []})
    chat_call = AsyncMock(side_effect=RuntimeError("boom"))
    judge_call = AsyncMock()

    ds = GraphRAGChatDataset(
        id="demo",
        version="1.0",
        corpus_id="demo",
        queries=_qs(),
        graph_provider=provider,
        graphrag_search=graphrag_search,
        chat=chat_call,
        judge=judge,
        judge_call=judge_call,
    )
    out = await ds.run(chat)
    assert out.error is None
    pqs = out.extras["per_query"]
    assert all("error" in pq for pq in pqs)
    judge_call.assert_not_called()


def test_judge_verdict_dataclass():
    v = JudgeVerdict(faithfulness=5, correctness=4, refusal_correct=None)
    assert v.faithfulness == 5
    assert v.refusal_correct is None
