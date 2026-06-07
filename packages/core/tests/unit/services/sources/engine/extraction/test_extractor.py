# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the extractor helper functions.

Focused on pure helpers (symmetric/inverse resolution) and the
embedding generation path with mocked dependencies.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.services.sources.engine.extraction.extractor import (
    _resolve_inverse_map,
    _resolve_symmetric_types,
    generate_embeddings,
    get_embedding_model_name,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_settings() -> SimpleNamespace:
    """Return a minimal fake settings object used by embedding tests."""
    return SimpleNamespace(
        source_processing=SimpleNamespace(entity_max_description_length=4000),
        embedding=SimpleNamespace(model="test-embedding-model"),
        extraction=SimpleNamespace(
            dedup_type_partition_cutoff=50,
            dedup_no_overlap_boost=0.08,
            dedup_borderline_penalty=0.05,
        ),
    )


def _batch_result(embeddings: list[list[float]]) -> SimpleNamespace:
    """Create a batch embedding result stub matching the real shape."""
    return SimpleNamespace(embeddings=embeddings)


# ---------------------------------------------------------------------------
# TestResolveSymmetricTypes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveSymmetricTypes:
    """Tests for _resolve_symmetric_types."""

    def test_none_domain_returns_none(self) -> None:
        """No domain name yields None."""
        assert _resolve_symmetric_types(None, lambda _: ["x"]) is None

    def test_none_resolver_returns_none(self) -> None:
        """Missing resolver callable yields None."""
        assert _resolve_symmetric_types("literary", None) is None

    def test_empty_list_returns_none(self) -> None:
        """An empty type list from the resolver is treated as None."""
        assert _resolve_symmetric_types("literary", lambda _: []) is None

    def test_lowercases_type_names(self) -> None:
        """Returned frozen set contains only lowercase names."""
        result = _resolve_symmetric_types("literary", lambda _: ["Spouse_Of", "Friend_Of"])
        assert result == frozenset({"spouse_of", "friend_of"})

    def test_resolver_exception_swallowed(self) -> None:
        """Exceptions in the resolver degrade to None."""

        def _raiser(_: str) -> list[str]:
            raise RuntimeError("fail")

        assert _resolve_symmetric_types("literary", _raiser) is None


# ---------------------------------------------------------------------------
# TestResolveInverseMap
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveInverseMap:
    """Tests for _resolve_inverse_map."""

    def test_none_domain_returns_none(self) -> None:
        """No domain name yields None."""
        assert _resolve_inverse_map(None, lambda _: {"a": "b"}) is None

    def test_none_resolver_returns_none(self) -> None:
        """Missing resolver callable yields None."""
        assert _resolve_inverse_map("literary", None) is None

    def test_empty_map_returns_none(self) -> None:
        """Empty dict from the resolver is treated as None."""
        assert _resolve_inverse_map("literary", lambda _: {}) is None

    def test_pass_through_dict(self) -> None:
        """Non-empty dict is returned verbatim."""
        result = _resolve_inverse_map("literary", lambda _: {"parent_of": "child_of"})
        assert result == {"parent_of": "child_of"}

    def test_resolver_exception_swallowed(self) -> None:
        """Exceptions in the resolver degrade to None."""

        def _raiser(_: str) -> dict[str, str]:
            raise RuntimeError("fail")

        assert _resolve_inverse_map("literary", _raiser) is None


# ---------------------------------------------------------------------------
# TestGetEmbeddingModelName
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetEmbeddingModelName:
    """Tests for get_embedding_model_name."""

    def test_returns_configured_model(self) -> None:
        """Configured embedding model name is returned as-is."""
        settings = SimpleNamespace(embedding=SimpleNamespace(model="my-model"))
        assert get_embedding_model_name(settings) == "my-model"


# ---------------------------------------------------------------------------
# TestGenerateEmbeddings
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestGenerateEmbeddings:
    """Tests for generate_embeddings."""

    async def test_no_service_returns_empty_summary(self) -> None:
        """Missing embedding service returns an empty summary dict."""
        result = await generate_embeddings(
            entities=[{"name": "Alice"}],
            embedding_service=None,
            settings=_fake_settings(),
        )
        assert result == {"count": 0, "model": "none", "dimensions": 0, "cached_count": 0}

    async def test_no_entities_returns_empty_summary(self) -> None:
        """Empty entities list returns an empty summary dict."""
        service = MagicMock()
        result = await generate_embeddings(
            entities=[],
            embedding_service=service,
            settings=_fake_settings(),
        )
        assert result == {"count": 0, "model": "none", "dimensions": 0, "cached_count": 0}

    async def test_reuses_cached_embeddings_when_lengths_match(self) -> None:
        """Cached embeddings matching entity count are reused, no batch_embed call."""
        service = MagicMock()
        service.batch_embed = AsyncMock()
        cached = [[0.1, 0.2], [0.3, 0.4]]
        entities = [
            {"name": "A", "type": "Person", "description": ""},
            {"name": "B", "type": "Person", "description": ""},
        ]
        result = await generate_embeddings(
            entities=entities,
            embedding_service=service,
            settings=_fake_settings(),
            cached_embeddings=cached,
        )
        service.batch_embed.assert_not_awaited()
        assert result["count"] == 2
        assert result["cached_count"] == 2
        assert result["dimensions"] == 2
        assert result["model"] == "test-embedding-model"
        assert result["embeddings"] == cached

    async def test_generates_new_when_no_cache(self) -> None:
        """When no cache is provided, embeddings are generated via batch_embed."""
        service = MagicMock()
        service.batch_embed = AsyncMock(
            return_value=_batch_result([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
        )
        entities = [
            {"name": "A", "type": "Person", "description": ""},
            {"name": "B", "type": "Person", "description": ""},
        ]
        result = await generate_embeddings(
            entities=entities,
            embedding_service=service,
            settings=_fake_settings(),
        )
        service.batch_embed.assert_awaited_once()
        assert result["count"] == 2
        assert result["cached_count"] == 0
        assert result["dimensions"] == 3

    async def test_regenerates_when_cache_length_mismatch(self) -> None:
        """Length mismatch between cache and entities triggers full regeneration."""
        service = MagicMock()
        service.batch_embed = AsyncMock(return_value=_batch_result([[1.0, 2.0]]))
        entities = [{"name": "A", "type": "Person", "description": ""}]
        # Cached has 2 entries but only 1 entity
        cached = [[0.1, 0.2], [0.3, 0.4]]
        result = await generate_embeddings(
            entities=entities,
            embedding_service=service,
            settings=_fake_settings(),
            cached_embeddings=cached,
        )
        service.batch_embed.assert_awaited_once()
        assert result["cached_count"] == 0
        assert result["count"] == 1
