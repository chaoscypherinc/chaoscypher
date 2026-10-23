# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Vector shaping: explicit, re-normalised MRL cuts and unit-length storage (audit R1, R7).

qwen3-embedding:4b returns 2560 dimensions. Until 2026-10-06 the Ollama
provider kept the first 1024 (``search.vector_dimensions``) without saying so
and without re-normalising, while the naive rung embedded at 2560.
"""

from __future__ import annotations

import math
import random
import struct
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from chaoscypher_core.adapters.embedding.ollama_provider import OllamaEmbeddingProvider
from chaoscypher_core.adapters.embedding.vectors import fit_dimensions, normalize_vector
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository
from chaoscypher_core.exceptions import LLMError


NATIVE = 2560


def _norm(vector: list[float] | tuple[float, ...]) -> float:
    """The L2 norm."""
    return math.sqrt(sum(v * v for v in vector))


def _raw(seed: int) -> list[float]:
    """A deterministic 2560-d vector that is not unit length (as Ollama's need not be)."""
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) * 3.0 for _ in range(NATIVE)]


def _provider(dims: int, vectors: list[list[float]]) -> tuple[OllamaEmbeddingProvider, MagicMock]:
    """An Ollama provider whose HTTP client answers ``/api/embed`` with ``vectors``."""
    response = MagicMock(status_code=200)
    response.json.return_value = {"embeddings": vectors}
    client = AsyncMock()
    client.post.return_value = response
    provider = OllamaEmbeddingProvider("qwen3-embedding:4b", dims, "http://ollama:11434")
    return provider, client


def test_fit_dimensions_cuts_renormalises_and_refuses_to_pad() -> None:
    cut = fit_dimensions([3.0, 4.0, 12.0], 2)
    assert cut == pytest.approx([0.6, 0.8])
    assert normalize_vector([0.0, 0.0]) == [0.0, 0.0]
    with pytest.raises(ValueError, match="fewer than"):
        fit_dimensions([1.0, 2.0], 3)


@pytest.mark.asyncio
async def test_a_2560_d_model_is_stored_consistently_and_unit_length(tmp_path: Path) -> None:
    """At native size and when cut to 1024, the stored vectors are unit length and a text finds itself."""
    raw = [_raw(1), _raw(2)]
    for dims in (NATIVE, 1024):
        provider, client = _provider(dims, raw)
        with patch(
            "chaoscypher_core.adapters.embedding.ollama_provider.httpx.AsyncClient",
            return_value=client,
        ):
            batch = await provider.batch_embed(["first", "second"])
        assert provider.native_dimensions == NATIVE
        assert all(len(v) == dims and _norm(v) == pytest.approx(1.0) for v in batch.embeddings)
        # the cut is the prefix of the native vector, re-normalised (never another embedding)
        assert batch.embeddings[0] == pytest.approx(normalize_vector(raw[0][:dims]), abs=1e-9)

        db = tmp_path / f"d{dims}" / "app.db"
        repo = SearchRepository(get_engine(str(db)), vector_dim=dims, embedding_model="q")
        try:
            assert (
                repo.index_embeddings_batch(
                    [("chunk:a", batch.embeddings[0]), ("chunk:b", batch.embeddings[1])],
                    item_type="chunk",
                )
                == 2
            )
            with repo._engine.connect() as conn:
                blobs = conn.execute(text("SELECT embedding FROM vec_search_chunks")).scalars()
                stored = [struct.unpack(f"<{dims}f", b) for b in blobs]
            assert all(_norm(v) == pytest.approx(1.0, abs=1e-5) for v in stored)
            hits = repo.vector_search(batch.embeddings[0], k=1, item_type="chunk")
            assert hits[0][0] == "chunk:a" and hits[0][1] == pytest.approx(1.0, abs=1e-5)
        finally:
            evict_engine(db)


@pytest.mark.asyncio
async def test_a_model_shorter_than_the_configured_size_is_refused() -> None:
    """A 768-d model under a 1024-d index fails loudly rather than mismatching every vector."""
    provider, client = _provider(1024, [[0.5] * 768])
    with (
        patch(
            "chaoscypher_core.adapters.embedding.ollama_provider.httpx.AsyncClient",
            return_value=client,
        ),
        pytest.raises(LLMError, match="fewer than"),
    ):
        await provider.embed("text")
