# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Every embedding provider returns unit vectors at ``vector_dimensions`` and declares its query prefix.

Until the wave-2 audit (2026-10-06) the OpenAI and Gemini providers returned
the API's vectors as given (Gemini's truncated ``outputDimensionality``
vectors are not unit length; an OpenAI-compatible server may ignore
``dimensions``), and ``query_vector_identity`` read a ``query_prefix`` no
provider defined. The guard discovers every provider class in the package, so
a new provider without a row here fails.
"""

from __future__ import annotations

import importlib
import inspect
import math
import pkgutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import chaoscypher_core.adapters.embedding as embedding_pkg
from chaoscypher_core.adapters.embedding.gemini_provider import GeminiEmbeddingProvider
from chaoscypher_core.adapters.embedding.local_provider import LocalEmbeddingProvider
from chaoscypher_core.adapters.embedding.ollama_provider import OllamaEmbeddingProvider
from chaoscypher_core.adapters.embedding.openai_provider import OpenAIEmbeddingProvider
from chaoscypher_core.adapters.embedding.vectors import fit_dimensions
from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol


RAW = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
"""What every fake backend returns: 8 values, not unit length."""
DIMS = 4


class _Client:
    """An httpx client stand-in answering embedding POSTs from the payload's input count."""

    is_closed = False

    def __init__(self, answer: Callable[[str, dict[str, Any]], dict[str, Any]]) -> None:
        self._answer = answer

    async def post(self, url: str, json: dict[str, Any], **_: Any) -> httpx.Response:
        return httpx.Response(200, json=self._answer(url, json))


def _ollama(tmp_path: Path) -> Any:
    provider = OllamaEmbeddingProvider("m", DIMS, base_url="http://ollama")
    provider._client = _Client(  # type: ignore[assignment]
        lambda _u, p: {
            "embeddings": [RAW] * (len(p["input"]) if isinstance(p["input"], list) else 1)
        }
    )
    return provider


def _openai(tmp_path: Path) -> Any:
    provider = OpenAIEmbeddingProvider("m", DIMS, api_key="k")
    provider._client = _Client(  # type: ignore[assignment]
        lambda _u, p: {"data": [{"embedding": RAW, "index": i} for i in range(len(p["input"]))]}
    )
    return provider


def _gemini(tmp_path: Path) -> Any:
    provider = GeminiEmbeddingProvider("m", DIMS, api_key="k")

    def answer(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if url.endswith(":batchEmbedContents"):
            return {"embeddings": [{"values": RAW} for _ in payload["requests"]]}
        return {"embedding": {"values": RAW}}

    provider._client = _Client(answer)  # type: ignore[assignment]
    return provider


def _local(tmp_path: Path) -> Any:
    provider = LocalEmbeddingProvider("m", DIMS, cache_dir=tmp_path)

    def encode(texts: Any, **_: Any) -> Any:
        return [RAW] * len(texts) if isinstance(texts, list) else RAW

    provider._ensure_model = lambda: SimpleNamespace(encode=encode)  # type: ignore[method-assign]
    return provider


PROVIDERS: dict[str, Callable[[Path], Any]] = {
    "OllamaEmbeddingProvider": _ollama,
    "OpenAIEmbeddingProvider": _openai,
    "GeminiEmbeddingProvider": _gemini,
    "LocalEmbeddingProvider": _local,
}


def _provider_classes() -> set[str]:
    """Every class in the embedding adapter package that embeds (has ``embed`` and ``batch_embed``)."""
    found: set[str] = set()
    for info in pkgutil.iter_modules(embedding_pkg.__path__):
        module = importlib.import_module(f"{embedding_pkg.__name__}.{info.name}")
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                obj.__module__ == module.__name__
                and hasattr(obj, "embed")
                and hasattr(obj, "batch_embed")
            ):
                found.add(name)
    return found


def test_every_provider_in_the_package_is_covered() -> None:
    assert _provider_classes() == set(PROVIDERS)


def _is_unit(vector: list[float]) -> bool:
    return math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(PROVIDERS))
async def test_every_provider_returns_unit_vectors_at_its_dimensions(
    name: str, tmp_path: Path
) -> None:
    provider = PROVIDERS[name](tmp_path)
    expected = fit_dimensions(RAW, DIMS)
    single = await provider.embed("Who?")
    assert single.embedding == pytest.approx(expected)
    assert _is_unit(single.embedding)
    batch = await provider.batch_embed(["a", "b", "c"], batch_size=2)
    assert len(batch.embeddings) == 3
    assert all(v == pytest.approx(expected) for v in batch.embeddings)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(PROVIDERS))
async def test_a_vector_shorter_than_configured_is_an_llm_error(name: str, tmp_path: Path) -> None:
    provider = PROVIDERS[name](tmp_path)
    provider.vector_dimensions = len(RAW) + 1
    call: Awaitable[Any] = provider.embed("Who?")
    with pytest.raises(LLMError, match="fewer than"):
        await call


@pytest.mark.parametrize("name", sorted(PROVIDERS))
def test_every_provider_declares_its_query_prefix(name: str, tmp_path: Path) -> None:
    """The protocol's ``query_prefix``: what the query-vector cache keys on, empty for every provider."""
    provider = PROVIDERS[name](tmp_path)
    assert isinstance(provider, EmbeddingProviderProtocol)
    assert provider.query_prefix == ""
