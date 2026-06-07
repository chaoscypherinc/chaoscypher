# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for CCX export metadata manager."""

from chaoscypher_core.services.export.management.metadata_manager import (
    build_chunk_dict,
)


class TestBuildChunkDictEmbeddings:
    """Tests for build_chunk_dict embedding handling."""

    def _make_chunk(self) -> dict:
        return {
            "id": "chunk_001",
            "chunk_index": 0,
            "content": "Some text content",
            "page_number": 1,
            "section": "intro",
            "status": "indexed",
            "embedding": b"\x00\x01\x02\x03",
            "embedding_model": "nomic-embed-text:v1.5",
            "embedding_dimensions": 768,
            "chunk_metadata": {"key": "value"},
            "created_at": "2026-01-01T00:00:00",
        }

    def test_include_embeddings_true(self):
        """When include_embeddings=True, embedding key is present."""
        chunk = self._make_chunk()
        result = build_chunk_dict(chunk, include_embeddings=True)
        assert "embedding" in result
        assert result["embedding"] == b"\x00\x01\x02\x03"
        assert result["embedding_model"] == "nomic-embed-text:v1.5"
        assert result["embedding_dimensions"] == 768

    def test_include_embeddings_false(self):
        """When include_embeddings=False, embedding key is omitted entirely."""
        chunk = self._make_chunk()
        result = build_chunk_dict(chunk, include_embeddings=False)
        assert "embedding" not in result
        assert result["embedding_model"] == "nomic-embed-text:v1.5"
        assert result["embedding_dimensions"] == 768

    def test_default_excludes_embeddings(self):
        """Default behavior excludes embeddings."""
        chunk = self._make_chunk()
        result = build_chunk_dict(chunk)
        assert "embedding" not in result

    def test_metadata_preserved_when_embeddings_excluded(self):
        """All non-embedding fields preserved when embeddings excluded."""
        chunk = self._make_chunk()
        result = build_chunk_dict(chunk, include_embeddings=False)
        assert result["id"] == "chunk_001"
        assert result["content"] == "Some text content"
        assert result["chunk_index"] == 0
        assert result["status"] == "indexed"
