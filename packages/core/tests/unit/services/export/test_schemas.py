# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for CCX export schema models."""

from chaoscypher_core.services.export.models.schemas import (
    EmbeddingStats,
    SourceStats,
)


class TestEmbeddingStatsVectorsIncluded:
    """Tests for EmbeddingStats.vectors_included field."""

    def test_vectors_included_required(self):
        """vectors_included is a required field."""
        stats = EmbeddingStats(
            is_present=True,
            vectors_included=True,
            node_count=10,
        )
        assert stats.vectors_included is True

    def test_vectors_not_included(self):
        """vectors_included=False when export excludes vectors."""
        stats = EmbeddingStats(
            is_present=True,
            vectors_included=False,
            node_count=10,
            dimensions=768,
            model_name="nomic-embed-text:v1.5",
        )
        assert stats.is_present is True
        assert stats.vectors_included is False
        assert stats.model_name == "nomic-embed-text:v1.5"

    def test_no_embeddings_at_all(self):
        """Both false when source never had embeddings."""
        stats = EmbeddingStats(
            is_present=False,
            vectors_included=False,
            node_count=0,
        )
        assert stats.is_present is False
        assert stats.vectors_included is False


class TestSourceStatsVectorsIncluded:
    """Tests for SourceStats.vectors_included field."""

    def test_vectors_included_required(self):
        """vectors_included is a required field."""
        stats = SourceStats(vectors_included=True)
        assert stats.vectors_included is True

    def test_vectors_not_included_preserves_metadata(self):
        """Metadata preserved when vectors excluded."""
        stats = SourceStats(
            vectors_included=False,
            chunks_with_embeddings=100,
            embedding_coverage_pct=95.0,
            embedding_models_used=["nomic-embed-text:v1.5"],
        )
        assert stats.vectors_included is False
        assert stats.chunks_with_embeddings == 100
        assert stats.embedding_models_used == ["nomic-embed-text:v1.5"]
