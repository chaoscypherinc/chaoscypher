# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for export stats embedding handling."""

from unittest.mock import MagicMock

from chaoscypher_core.services.export.engine.stats import (
    calculate_knowledge_stats,
    calculate_source_stats,
)


class TestKnowledgeStatsVectorsIncluded:
    """Tests for vectors_included in knowledge stats."""

    def _make_settings(self):
        """Create mock settings with embedding configuration."""
        settings = MagicMock()
        settings.embedding.model = "nomic-embed-text:v1.5"
        settings.embedding.provider = "ollama"
        return settings

    def test_vectors_included_true(self):
        """vectors_included=True when include_embeddings=True."""
        nodes = [{"id": "n1", "embedding": [0.1, 0.2], "template_id": "t1", "created_at": None}]
        stats = calculate_knowledge_stats(
            nodes=nodes,
            edges=[],
            settings=self._make_settings(),
            include_embeddings=True,
        )
        assert stats.embeddings.vectors_included is True

    def test_vectors_included_false(self):
        """vectors_included=False when include_embeddings=False."""
        nodes = [{"id": "n1", "embedding": [0.1, 0.2], "template_id": "t1", "created_at": None}]
        stats = calculate_knowledge_stats(
            nodes=nodes,
            edges=[],
            settings=self._make_settings(),
            include_embeddings=False,
        )
        assert stats.embeddings.is_present is True
        assert stats.embeddings.vectors_included is False

    def test_no_embeddings_vectors_included_false(self):
        """vectors_included=False when no embeddings exist even with include=True."""
        nodes = [{"id": "n1", "template_id": "t1", "created_at": None}]
        stats = calculate_knowledge_stats(
            nodes=nodes,
            edges=[],
            settings=self._make_settings(),
            include_embeddings=True,
        )
        assert stats.embeddings.is_present is False
        assert stats.embeddings.vectors_included is False


class TestSourceStatsVectorsIncluded:
    """Tests for vectors_included in source stats."""

    def test_vectors_included_true(self):
        """vectors_included=True when include_embeddings=True."""
        sources = [
            {
                "id": "s1",
                "chunks": [{"embedding": b"\x00", "content": "x"}],
                "citations": [],
                "tags": [],
                "created_at": None,
            }
        ]
        stats = calculate_source_stats(sources=sources, include_embeddings=True)
        assert stats.vectors_included is True

    def test_vectors_included_false(self):
        """vectors_included=False when include_embeddings=False."""
        sources = [
            {
                "id": "s1",
                "chunks": [{"content": "x"}],
                "citations": [],
                "tags": [],
                "created_at": None,
            }
        ]
        stats = calculate_source_stats(sources=sources, include_embeddings=False)
        assert stats.vectors_included is False

    def test_empty_sources_vectors_included_false(self):
        """Empty sources return vectors_included=False."""
        stats = calculate_source_stats(sources=[], include_embeddings=True)
        assert stats.vectors_included is False
