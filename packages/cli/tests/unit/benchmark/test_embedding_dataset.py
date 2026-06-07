# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_cli.benchmark.embedding_dataset import (
    EmbeddingRetrievalDataset,
    resolve_gold_entity,
)
from chaoscypher_cli.benchmark.models import ModelConfig
from chaoscypher_cli.benchmark.queries import LabeledQuery, LabeledQuerySet


def _make_qs() -> LabeledQuerySet:
    return LabeledQuerySet(
        version="1.0",
        queries=[
            LabeledQuery(
                id="q1",
                band="factual_single_hop",
                question="who funded ARPANET?",
                gold_entities=["ARPA"],
                gold_answer="ARPA did.",
            ),
            LabeledQuery(
                id="q2",
                band="out_of_scope",
                question="off-topic?",
                expect_refusal=True,
            ),
        ],
    )


def test_resolve_gold_exact_name():
    entities = [{"id": "uuid-1", "name": "ARPA", "aliases": []}]
    assert resolve_gold_entity("ARPA", entities) == "uuid-1"


def test_resolve_gold_case_insensitive():
    entities = [{"id": "uuid-1", "name": "Arpa", "aliases": []}]
    assert resolve_gold_entity("ARPA", entities) == "uuid-1"


def test_resolve_gold_via_alias():
    entities = [
        {
            "id": "uuid-1",
            "name": "Advanced Research Projects Agency",
            "aliases": ["ARPA", "DARPA"],
        }
    ]
    assert resolve_gold_entity("ARPA", entities) == "uuid-1"


def test_resolve_gold_normalized_fallback():
    entities = [{"id": "uuid-1", "name": "ARPA  Net", "aliases": []}]
    assert resolve_gold_entity("arpa net", entities) == "uuid-1"


def test_resolve_gold_unresolved():
    entities = [{"id": "uuid-1", "name": "UCLA", "aliases": []}]
    assert resolve_gold_entity("ARPA", entities) is None


@pytest.mark.asyncio
async def test_run_records_ranks_and_skips_out_of_scope():
    embedder = ModelConfig(provider="ollama", model="nomic", label="N")
    fake_ctx = MagicMock()
    fake_ctx.storage_adapter.list_entities = MagicMock(
        return_value=[{"id": "uuid-1", "name": "ARPA", "aliases": []}]
    )
    indexed = MagicMock()
    indexed.ctx = fake_ctx

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder):
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph
    embed_one = AsyncMock(return_value=[0.1, 0.2])
    vector_search = AsyncMock(return_value=[("uuid-1", 0.9), ("uuid-2", 0.5)])

    ds = EmbeddingRetrievalDataset(
        id="demo",
        version="1.0",
        corpus_id="demo",
        queries=_make_qs(),
        graph_provider=provider,
        embed_query=embed_one,
        vector_search=vector_search,
        top_k=10,
    )
    out = await ds.run(embedder)
    assert out.error is None
    per_query = out.extras["per_query"]
    assert len(per_query) == 1  # out_of_scope skipped
    assert per_query[0]["query_id"] == "q1"
    assert per_query[0]["ranks"]["ARPA"] == 1
    assert per_query[0]["skipped"] is False
