# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the FakeEmbeddingProvider test helper.

Pins the helper's two production-shape invariants:

1. ``BatchEmbedResult.embeddings`` is ``list[list[float]]`` (per the
   Pydantic model + the real local provider), so consumers that expect
   Python lists do not break.
2. Embeddings are deterministic across calls and runs (same text → same
   vector), so journey assertions don't flake.

See the mocked pipeline test fixtures.
"""

from __future__ import annotations

import pytest

from tests.fakes.embedding import FakeEmbeddingProvider


@pytest.mark.asyncio
async def test_batch_embed_returns_list_of_lists_of_floats() -> None:
    fake = FakeEmbeddingProvider(dimensions=8)

    result = await fake.batch_embed(["hello", "world"])

    assert isinstance(result.embeddings, list)
    assert len(result.embeddings) == 2
    for vec in result.embeddings:
        assert isinstance(vec, list)
        assert len(vec) == 8
        for v in vec:
            assert isinstance(v, float)


@pytest.mark.asyncio
async def test_batch_embed_is_deterministic_across_calls() -> None:
    fake = FakeEmbeddingProvider(dimensions=4)

    first = await fake.batch_embed(["alice", "bob"])
    second = await fake.batch_embed(["alice", "bob"])

    assert first.embeddings == second.embeddings


@pytest.mark.asyncio
async def test_batch_embed_distinct_texts_produce_distinct_vectors() -> None:
    fake = FakeEmbeddingProvider(dimensions=8)

    result = await fake.batch_embed(["alice", "bob"])

    assert result.embeddings[0] != result.embeddings[1]


@pytest.mark.asyncio
async def test_embed_single_text_matches_batch_embed_shape() -> None:
    fake = FakeEmbeddingProvider(dimensions=4)

    single = await fake.embed("alice")
    batched = await fake.batch_embed(["alice"])

    assert single.embedding == batched.embeddings[0]


@pytest.mark.asyncio
async def test_call_count_increments_per_call() -> None:
    fake = FakeEmbeddingProvider()

    assert fake.call_count == 0
    await fake.batch_embed(["a"])
    assert fake.call_count == 1
    await fake.embed("b")
    assert fake.call_count == 2


@pytest.mark.asyncio
async def test_batch_embed_returns_failed_zero() -> None:
    fake = FakeEmbeddingProvider()
    result = await fake.batch_embed(["a", "b", "c"])
    assert result.failed == 0
    assert result.total == 3


def test_provider_type_is_fake() -> None:
    fake = FakeEmbeddingProvider()
    assert fake.provider_type == "fake"


def test_model_name_attribute() -> None:
    fake = FakeEmbeddingProvider()
    assert fake.model_name == "fake-embed-test"


@pytest.mark.asyncio
async def test_dimensions_override_respected() -> None:
    fake = FakeEmbeddingProvider(dimensions=16)
    result = await fake.batch_embed(["hi"])
    assert len(result.embeddings[0]) == 16
