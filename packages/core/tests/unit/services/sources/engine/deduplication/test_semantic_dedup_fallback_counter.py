# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SEMANTIC_DEDUP_FALLBACKS counter wiring in EntityProcessor.

When the embedding step raises an exception inside
``deduplicate_entities_semantic``, the method falls back silently to
exact-string dedup.  Every such swallow must increment
``QualityCounter.SEMANTIC_DEDUP_FALLBACKS`` when an adapter + source_id
are provided.

When adapter or source_id is None the increment is a no-op — callers
without a source row (CLI / notebook / test) pass None and no DB write is
attempted.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.quality.counters import QualityCounter
from chaoscypher_core.services.sources.engine.deduplication import service as dedup_service_mod
from chaoscypher_core.services.sources.engine.deduplication.service import EntityProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entities(n: int = 3) -> list[dict]:
    return [{"name": f"Entity{i}", "type": "Person", "description": f"desc {i}"} for i in range(n)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSemanticDedupFallbackCounter:
    """SEMANTIC_DEDUP_FALLBACKS incremented when embedding step raises."""

    @pytest.mark.asyncio
    async def test_increments_once_on_embedding_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Embedding failure → SEMANTIC_DEDUP_FALLBACKS incremented once."""
        bumps: list[QualityCounter] = []

        async def fake_increment(
            *,
            adapter: Any,
            source_id: str,
            database_name: str,
            counter: QualityCounter,
            n: int = 1,
        ) -> None:
            bumps.extend([counter] * n)

        monkeypatch.setattr(dedup_service_mod, "increment_quality_counter", fake_increment)

        async def _failing_generate_embeddings(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("embedding service unavailable")

        monkeypatch.setattr(
            dedup_service_mod, "generate_entity_embeddings", _failing_generate_embeddings
        )

        processor = EntityProcessor()
        entities = _make_entities(3)
        adapter = MagicMock()

        unique, mapping, embeddings = await processor.deduplicate_entities_semantic(
            entities,
            embedding_service=MagicMock(),
            adapter=adapter,
            source_id="src-001",
            database_name="default",
        )

        assert bumps.count(QualityCounter.SEMANTIC_DEDUP_FALLBACKS) == 1
        # Fallback runs exact dedup — all distinct names survive
        assert len(unique) == 3
        # Embeddings list is empty (no embeddings available after fallback)
        assert embeddings == []

    @pytest.mark.asyncio
    async def test_no_increment_when_adapter_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Counter is skipped when adapter=None (standalone / no source row)."""
        bumps: list[QualityCounter] = []

        async def fake_increment(**_kwargs: Any) -> None:
            bumps.append(_kwargs["counter"])

        monkeypatch.setattr(dedup_service_mod, "increment_quality_counter", fake_increment)

        async def _failing_generate_embeddings(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("fail")

        monkeypatch.setattr(
            dedup_service_mod, "generate_entity_embeddings", _failing_generate_embeddings
        )

        processor = EntityProcessor()
        await processor.deduplicate_entities_semantic(
            _make_entities(2),
            embedding_service=MagicMock(),
            adapter=None,  # no adapter → no counter write
            source_id="src-002",
            database_name="default",
        )

        assert bumps == []

    @pytest.mark.asyncio
    async def test_no_increment_when_source_id_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Counter is skipped when source_id=None even with a real adapter."""
        bumps: list[QualityCounter] = []

        async def fake_increment(**_kwargs: Any) -> None:
            bumps.append(_kwargs["counter"])

        monkeypatch.setattr(dedup_service_mod, "increment_quality_counter", fake_increment)

        async def _failing_generate_embeddings(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("fail")

        monkeypatch.setattr(
            dedup_service_mod, "generate_entity_embeddings", _failing_generate_embeddings
        )

        processor = EntityProcessor()
        await processor.deduplicate_entities_semantic(
            _make_entities(2),
            embedding_service=MagicMock(),
            adapter=MagicMock(),
            source_id=None,  # no source_id → no counter write
            database_name="default",
        )

        assert bumps == []

    @pytest.mark.asyncio
    async def test_exact_dedup_fallback_runs_after_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fallback to exact-string dedup merges duplicate names correctly."""
        bumps: list[QualityCounter] = []

        async def fake_increment(**_kwargs: Any) -> None:
            bumps.append(_kwargs["counter"])

        monkeypatch.setattr(dedup_service_mod, "increment_quality_counter", fake_increment)

        async def _failing_generate_embeddings(*_args: Any, **_kwargs: Any) -> Any:
            raise ValueError("timeout")

        monkeypatch.setattr(
            dedup_service_mod, "generate_entity_embeddings", _failing_generate_embeddings
        )

        processor = EntityProcessor()
        # Two entities with identical name+type should be merged by exact dedup
        entities = [
            {"name": "Alice", "type": "Person", "description": "protagonist"},
            {"name": "Alice", "type": "Person", "description": "main character"},
            {"name": "Bob", "type": "Person", "description": "antagonist"},
        ]
        adapter = MagicMock()
        unique, mapping, embeddings = await processor.deduplicate_entities_semantic(
            entities,
            embedding_service=MagicMock(),
            adapter=adapter,
            source_id="src-003",
            database_name="mydb",
        )

        assert bumps.count(QualityCounter.SEMANTIC_DEDUP_FALLBACKS) == 1
        # Exact dedup merged "Alice" duplicates → 2 unique entities
        assert len(unique) == 2
        assert embeddings == []

    @pytest.mark.asyncio
    async def test_no_increment_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Counter is NOT incremented when the embedding step succeeds."""
        bumps: list[QualityCounter] = []

        async def fake_increment(**_kwargs: Any) -> None:
            bumps.append(_kwargs["counter"])

        monkeypatch.setattr(dedup_service_mod, "increment_quality_counter", fake_increment)

        # Fake embeddings that return zeros — the similarity matrix will
        # produce no merges, which is fine for this test.
        import numpy as np

        async def _ok_generate_embeddings(
            texts: list[str], *_a: Any, **_kw: Any
        ) -> tuple[list[list[float]], Any]:
            n = len(texts)
            vecs = [[0.0] * 4 for _ in range(n)]
            mat = np.zeros((n, 4))
            return vecs, mat

        monkeypatch.setattr(
            dedup_service_mod, "generate_entity_embeddings", _ok_generate_embeddings
        )

        processor = EntityProcessor()
        adapter = MagicMock()
        await processor.deduplicate_entities_semantic(
            _make_entities(2),
            embedding_service=MagicMock(),
            adapter=adapter,
            source_id="src-004",
            database_name="default",
        )

        assert QualityCounter.SEMANTIC_DEDUP_FALLBACKS not in bumps
