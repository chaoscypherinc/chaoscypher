# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the precomputed-embeddings cache-hit path in
``deduplicate_entities_semantic``.

When the chunk handler eager-writes per-chunk embeddings (or the finalizer
backfill computes them), the aggregated list flows into ``run_deduplication``
as ``precomputed_embeddings`` — and ``deduplicate_entities_semantic`` should
SKIP its own ``embedding_service.batch_embed`` call when the lengths line up.

Length mismatch falls back to the live path (defensive guard against any
future refactor breaking the single-writer co-write invariant).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from chaoscypher_core.services.sources.engine.deduplication import service as dedup_service_mod
from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor


def _make_entities(n: int) -> list[dict]:
    return [{"name": f"Entity{i}", "type": "Person", "description": f"desc {i}"} for i in range(n)]


def _make_zero_embeddings(n: int, dim: int = 4) -> list[list[float]]:
    return [[0.0] * dim for _ in range(n)]


class TestPrecomputedEmbeddingsCacheHit:
    """When precomputed embeddings are passed and lengths match, batch_embed is skipped."""

    @pytest.mark.asyncio
    async def test_skips_batch_embed_when_lengths_match(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cache hit path must NOT call ``generate_entity_embeddings``."""
        live_calls: list[Any] = []

        async def _record_live_path(*_args: Any, **_kwargs: Any) -> Any:
            live_calls.append(_args)
            n = len(_args[0])
            return _make_zero_embeddings(n), np.zeros((n, 4))

        monkeypatch.setattr(dedup_service_mod, "generate_entity_embeddings", _record_live_path)

        processor = EntityProcessor()
        entities = _make_entities(3)
        precomputed = _make_zero_embeddings(3)

        unique, _mapping, embeddings = await processor.deduplicate_entities_semantic(
            entities,
            embedding_service=MagicMock(),
            precomputed_embeddings=precomputed,
        )

        # The cache hit means we never enter the live-embed path.
        assert live_calls == [], "generate_entity_embeddings should not be called on cache hit"
        # Survivors and embeddings still flow through.
        assert len(unique) == 3
        assert len(embeddings) == 3

    @pytest.mark.asyncio
    async def test_falls_back_to_live_path_on_length_mismatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Length mismatch is treated as cache miss — live path runs."""
        live_calls: list[Any] = []

        async def _record_live_path(*_args: Any, **_kwargs: Any) -> Any:
            live_calls.append(_args)
            n = len(_args[0])
            return _make_zero_embeddings(n), np.zeros((n, 4))

        monkeypatch.setattr(dedup_service_mod, "generate_entity_embeddings", _record_live_path)

        processor = EntityProcessor()
        entities = _make_entities(3)
        # 2 precomputed for 3 entities → mismatch → fall back
        precomputed = _make_zero_embeddings(2)

        await processor.deduplicate_entities_semantic(
            entities,
            embedding_service=MagicMock(),
            precomputed_embeddings=precomputed,
        )

        assert len(live_calls) == 1, "live path must run when lengths mismatch"

    @pytest.mark.asyncio
    async def test_falls_back_to_live_path_when_precomputed_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No precomputed → live path runs (default behavior preserved)."""
        live_calls: list[Any] = []

        async def _record_live_path(*_args: Any, **_kwargs: Any) -> Any:
            live_calls.append(_args)
            n = len(_args[0])
            return _make_zero_embeddings(n), np.zeros((n, 4))

        monkeypatch.setattr(dedup_service_mod, "generate_entity_embeddings", _record_live_path)

        processor = EntityProcessor()
        entities = _make_entities(2)

        await processor.deduplicate_entities_semantic(
            entities,
            embedding_service=MagicMock(),
            precomputed_embeddings=None,
        )

        assert len(live_calls) == 1
