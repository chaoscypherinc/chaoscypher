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
    k1 = cache_key(corpus_id="ds1", corpus_version="1.0", extractor=m, seed=42, temperature=0.0)
    k2 = cache_key(corpus_id="ds1", corpus_version="1.0", extractor=m, seed=42, temperature=0.0)
    assert k1 == k2


def test_cache_key_changes_on_extractor():
    a = ModelConfig(provider="ollama", model="llama", label="L")
    b = ModelConfig(provider="ollama", model="qwen", label="Q")
    assert cache_key(
        corpus_id="ds1", corpus_version="1.0", extractor=a, seed=42, temperature=0.0
    ) != cache_key(corpus_id="ds1", corpus_version="1.0", extractor=b, seed=42, temperature=0.0)


def test_cache_key_changes_on_corpus_version():
    m = ModelConfig(provider="ollama", model="llama", label="L")
    assert cache_key(
        corpus_id="ds1", corpus_version="1.0", extractor=m, seed=42, temperature=0.0
    ) != cache_key(corpus_id="ds1", corpus_version="2.0", extractor=m, seed=42, temperature=0.0)


def test_cache_key_changes_on_seed_and_temperature():
    """The pins are part of the key: a ``--seed 7`` run, or a graph extracted
    before #650 pinned temperature 0.0, must not be served to stages 2/3 of a
    run that asked for different decoding (2026-09-24 llm audit).
    """
    m = ModelConfig(provider="ollama", model="llama", label="L")
    base = cache_key(corpus_id="ds1", corpus_version="1.0", extractor=m, seed=42, temperature=0.0)
    assert base != cache_key(
        corpus_id="ds1", corpus_version="1.0", extractor=m, seed=7, temperature=0.0
    )
    assert base != cache_key(
        corpus_id="ds1", corpus_version="1.0", extractor=m, seed=42, temperature=0.1
    )


@pytest.mark.asyncio
async def test_get_or_build_calls_builder_on_miss(tmp_path):
    cache = GraphCache(root=tmp_path / "cache")
    m = ModelConfig(provider="ollama", model="llama", label="L")

    async def fake_build(target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"sqlite-snapshot-data")  # noqa: ASYNC240

    out = await cache.get_or_build(
        corpus_id="ds1",
        corpus_version="1.0",
        extractor=m,
        seed=42,
        temperature=0.0,
        builder=fake_build,
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

    await cache.get_or_build(
        corpus_id="ds1",
        corpus_version="1.0",
        extractor=m,
        seed=42,
        temperature=0.0,
        builder=fake_build,
    )
    await cache.get_or_build(
        corpus_id="ds1",
        corpus_version="1.0",
        extractor=m,
        seed=42,
        temperature=0.0,
        builder=builder,
    )
    builder.assert_not_called()


def test_clear_removes_root(tmp_path):
    cache = GraphCache(root=tmp_path / "cache")
    (cache._root).mkdir(parents=True)
    (cache._root / "marker").write_text("x")
    cache.clear()
    assert not (cache._root / "marker").exists()


def test_has_reports_whether_slot_holds_a_snapshot(tmp_path):
    cache = GraphCache(root=tmp_path / "cache")
    ext = ModelConfig(provider="ollama", model="m", label="M")
    other = ModelConfig(provider="ollama", model="n", label="N")
    pins = {"seed": 42, "temperature": 0.0}
    assert not cache.has(corpus_id="c", corpus_version="1", extractor=ext, **pins)

    key = cache.key_for(corpus_id="c", corpus_version="1", extractor=ext, **pins)
    assert key == cache_key(corpus_id="c", corpus_version="1", extractor=ext, **pins)
    # An empty slot directory is not a snapshot.
    (tmp_path / "cache" / key).mkdir(parents=True)
    assert not cache.has(corpus_id="c", corpus_version="1", extractor=ext, **pins)

    (tmp_path / "cache" / key / "app.db").write_bytes(b"x")
    assert cache.has(corpus_id="c", corpus_version="1", extractor=ext, **pins)
    assert not cache.has(corpus_id="c", corpus_version="2", extractor=ext, **pins)
    assert not cache.has(corpus_id="c", corpus_version="1", extractor=other, **pins)
    # The decoding pins are part of the slot: another seed or temperature is a miss.
    assert not cache.has(corpus_id="c", corpus_version="1", extractor=ext, seed=7, temperature=0.0)
    assert not cache.has(corpus_id="c", corpus_version="1", extractor=ext, seed=42, temperature=0.7)
