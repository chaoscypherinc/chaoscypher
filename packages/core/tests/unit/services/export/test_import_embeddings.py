# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for import embedding compatibility detection."""

from chaoscypher_core.services.package.importer.models import ImportStats


class TestImportStatsEmbeddingFields:
    """Tests for ImportStats embedding regeneration tracking."""

    def test_default_no_regeneration_needed(self):
        """By default, no regeneration needed."""
        stats = ImportStats()
        assert stats.embeddings_need_regeneration is False
        assert stats.embedding_mismatch_reason is None

    def test_regeneration_flagged(self):
        """Can flag that regeneration is needed."""
        stats = ImportStats()
        stats.embeddings_need_regeneration = True
        stats.embedding_mismatch_reason = (
            "model mismatch: nomic-embed-text vs text-embedding-3-small"
        )
        assert stats.embeddings_need_regeneration is True

    def test_to_dict_includes_embedding_fields(self):
        """to_dict includes embedding regeneration fields."""
        stats = ImportStats(
            embeddings_need_regeneration=True, embedding_mismatch_reason="no vectors"
        )
        d = stats.to_dict()
        assert d["embeddings_need_regeneration"] is True
        assert d["embedding_mismatch_reason"] == "no vectors"
