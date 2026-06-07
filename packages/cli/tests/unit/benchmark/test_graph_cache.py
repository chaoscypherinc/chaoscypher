# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from chaoscypher_cli.benchmark.graph_cache import (
    GraphCache,
    cache_key,
)
from chaoscypher_cli.benchmark.models import ModelConfig


def test_cache_key_stable():
    m = ModelConfig(provider="ollama", model="llama3.1:8b", label="L")
    k1 = cache_key(corpus_id="ds1", corpus_version="1.0", extractor=m)
    k2 = cache_key(corpus_id="ds1", corpus_version="1.0", extractor=m)
    assert k1 == k2


def test_cache_key_changes_on_extractor():
    a = ModelConfig(provider="ollama", model="llama", label="L")
    b = ModelConfig(provider="ollama", model="qwen", label="Q")
    assert cache_key(corpus_id="ds1", corpus_version="1.0", extractor=a) != cache_key(
        corpus_id="ds1", corpus_version="1.0", extractor=b
    )


def test_cache_key_changes_on_corpus_version():
    m = ModelConfig(provider="ollama", model="llama", label="L")
    assert cache_key(corpus_id="ds1", corpus_version="1.0", extractor=m) != cache_key(
        corpus_id="ds1", corpus_version="2.0", extractor=m
    )


@pytest.mark.asyncio
async def test_get_or_build_calls_builder_on_miss(tmp_path):
    cache = GraphCache(root=tmp_path / "cache")
    m = ModelConfig(provider="ollama", model="llama", label="L")

    async def fake_build(target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"sqlite-snapshot-data")  # noqa: ASYNC240

    out = await cache.get_or_build(
        corpus_id="ds1", corpus_version="1.0", extractor=m, builder=fake_build
    )
    assert out.exists()
    assert out.read_bytes() == b"sqlite-snapshot-data"


@pytest.mark.asyncio
async def test_get_or_build_skips_builder_on_hit(tmp_path):
    cache = GraphCache(root=tmp_path / "cache")
    m = ModelConfig(provider="ollama", model="llama", label="L")

    builder = AsyncMock()

    async def fake_build(target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"v1")  # noqa: ASYNC240

    await cache.get_or_build(corpus_id="ds1", corpus_version="1.0", extractor=m, builder=fake_build)
    await cache.get_or_build(corpus_id="ds1", corpus_version="1.0", extractor=m, builder=builder)
    builder.assert_not_called()


def test_clear_removes_root(tmp_path):
    cache = GraphCache(root=tmp_path / "cache")
    (cache._root).mkdir(parents=True)
    (cache._root / "marker").write_text("x")
    cache.clear()
    assert not (cache._root / "marker").exists()
