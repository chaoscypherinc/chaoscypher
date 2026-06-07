# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for summarize tool clustering logic."""

import base64

import numpy as np

from chaoscypher_core.services.workflows.tools.engine.handlers.summarize_handlers import (
    SummarizeToolHandlers,
)


def _make_chunk(index: int, embedding: list[float], source_id: str = "src1") -> dict:
    """Create a mock chunk dict with base64-encoded embedding."""
    arr = np.array(embedding, dtype=np.float32)
    encoded = base64.b64encode(arr.tobytes()).decode("utf-8")
    return {
        "id": f"chunk-{index}",
        "chunk_index": index,
        "content": f"Content of chunk {index}",
        "embedding": encoded,
        "source_id": source_id,
        "page_number": None,
        "section": None,
    }


class TestClustering:
    """Test representative chunk selection via K-Means clustering."""

    def test_select_representatives_returns_k_chunks(self):
        """Should return exactly k representative chunks."""
        # Create 10 chunks with distinct embeddings in 3 clusters
        chunks = [_make_chunk(i, [1.0 + i * 0.1, 0.0, 0.0]) for i in range(4)]
        chunks.extend(_make_chunk(i, [0.0, 1.0 + (i - 4) * 0.1, 0.0]) for i in range(4, 7))
        chunks.extend(_make_chunk(i, [0.0, 0.0, 1.0 + (i - 7) * 0.1]) for i in range(7, 10))

        result = SummarizeToolHandlers._select_representatives(chunks, k=3)
        assert len(result) == 3

    def test_select_representatives_sorted_by_chunk_index(self):
        """Representatives should be sorted by original document order."""
        chunks = []
        for i in range(10):
            emb = [0.0] * 3
            emb[i % 3] = 1.0 + i * 0.01
            chunks.append(_make_chunk(i, emb))

        result = SummarizeToolHandlers._select_representatives(chunks, k=3)
        indices = [c["chunk_index"] for c in result]
        assert indices == sorted(indices)

    def test_select_representatives_k_equals_num_chunks(self):
        """When k >= num_chunks, return all chunks."""
        chunks = [_make_chunk(i, [float(i), 0.0]) for i in range(5)]
        result = SummarizeToolHandlers._select_representatives(chunks, k=5)
        assert len(result) == 5

    def test_select_representatives_skips_missing_embeddings(self):
        """Chunks without embeddings should be skipped during clustering."""
        chunks = [_make_chunk(i, [float(i), 0.0]) for i in range(5)]
        chunks[2]["embedding"] = None  # Missing embedding

        result = SummarizeToolHandlers._select_representatives(chunks, k=3)
        assert len(result) <= 3
        assert all(c["embedding"] is not None for c in result)

    def test_select_representatives_per_source(self):
        """Multi-source should cluster per document."""
        chunks_a = [_make_chunk(i, [float(i), 0.0], source_id="docA") for i in range(6)]
        chunks_b = [_make_chunk(i, [0.0, float(i)], source_id="docB") for i in range(6)]

        result = SummarizeToolHandlers._select_representatives_per_source(
            chunks_a + chunks_b, k_per_source=3
        )
        source_ids = [c["source_id"] for c in result]
        assert source_ids.count("docA") == 3
        assert source_ids.count("docB") == 3
