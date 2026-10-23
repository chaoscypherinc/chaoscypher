# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The cache is an SDK surface: usable from Core alone, with no app package on the import path.

``test_import_boundary`` reads the cache's own import statements; these tests
run it. Importing ``chaoscypher_core.cache`` in a fresh interpreter, and using
it from this one, must never import Cortex, Neuron or the CLI.
"""

from __future__ import annotations

import importlib
import importlib.abc
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import chaoscypher_core
from chaoscypher_core.models import BatchEmbedResult, EmbedResult


_APP_PACKAGES = ("chaoscypher_cortex", "chaoscypher_neuron", "chaoscypher_cli")
_CAP = 1 << 24


def _is_app_module(name: str) -> bool:
    """Whether ``name`` is an app package or one of its modules."""
    return name.split(".", 1)[0] in _APP_PACKAGES


def _is_cache_module(name: str) -> bool:
    """Whether ``name`` is the cache package or one of its modules."""
    return name == "chaoscypher_core.cache" or name.startswith("chaoscypher_core.cache.")


class _AppImportBlocker(importlib.abc.MetaPathFinder):
    """A finder that refuses every app-package import."""

    def find_spec(self, fullname: str, path: Any, target: Any = None) -> None:
        """Raise on an app-package import; leave every other import to the next finder."""
        if _is_app_module(fullname):
            msg = f"the cache SDK imported {fullname}"
            raise ImportError(msg)


@pytest.fixture
def guarded_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """``chaoscypher_core.cache`` imported afresh while any app-package import raises.

    The guard stays on for the whole test, so a lazy import inside a call is
    caught too; ``sys.modules`` is restored afterwards.
    """
    saved = dict(sys.modules)
    for name in [n for n in sys.modules if _is_app_module(n) or _is_cache_module(n)]:
        del sys.modules[name]
    monkeypatch.setattr(sys, "meta_path", [_AppImportBlocker(), *sys.meta_path])
    monkeypatch.setattr(chaoscypher_core, "cache", getattr(chaoscypher_core, "cache", None))
    try:
        yield importlib.import_module("chaoscypher_core.cache")
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


class _FakeChat:
    """A hosted-style chat provider pinned at temperature 0 that counts its calls."""

    def __init__(self) -> None:
        self.config: dict[str, Any] = {"chat_provider": "fake", "ai_temperature": 0.0}
        self.chat_model = "fake-model-2026-10-06"
        self.calls = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        stream: bool = False,
        enable_thinking: bool = False,
        high_priority: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """A clean, finished answer."""
        self.calls += 1
        return {"content": "Pierre.", "finish_reason": "stop", "model": self.chat_model}


class _FakeEmbedder:
    """An embedding provider that answers one vector per text and counts its calls."""

    provider_type = "fake"
    query_prefix = ""

    def __init__(self) -> None:
        self.model_name = "fake-embedder"
        self.calls = 0

    async def embed(self, text: str) -> EmbedResult:
        """The vector of ``text``."""
        self.calls += 1
        return EmbedResult(embedding=[float(len(text)), 0.5], provider=self.provider_type)

    async def batch_embed(self, texts: list[str], batch_size: int = 64) -> BatchEmbedResult:
        """Every text's vector."""
        self.calls += 1
        return BatchEmbedResult(
            embeddings=[[float(len(t)), 0.5] for t in texts],
            total=len(texts),
            provider=self.provider_type,
        )


def test_importing_the_cache_in_a_fresh_interpreter_loads_no_app_package() -> None:
    script = (
        "import sys\n"
        f"for name in {_APP_PACKAGES!r}:\n"
        "    sys.modules[name] = None  # any import of it raises\n"
        "import chaoscypher_core.cache\n"
        "loaded = {n.split('.')[0] for n, m in sys.modules.items() if m and n.startswith('chaoscypher')}\n"
        "print(','.join(sorted(loaded)))\n"
    )
    result = subprocess.run(  # noqa: S603 - the test's own interpreter and script
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "chaoscypher_core"


async def test_a_completion_and_an_embedding_round_trip_through_a_store_on_disk(
    guarded_cache: ModuleType, tmp_path: Path
) -> None:
    cache = guarded_cache
    messages = [{"role": "user", "content": "Who kept the accounts?"}]

    chat_store = cache.SqliteCacheStore(tmp_path / "completions.db", max_bytes=_CAP)
    chat_inner = _FakeChat()
    chat = cache.CachedChatProvider(chat_inner, chat_store)
    first = await chat.chat(messages)
    second = await chat.chat(messages)
    assert (
        first
        == second
        == {"content": "Pierre.", "finish_reason": "stop", "model": chat_inner.chat_model}
    )
    assert chat_inner.calls == 1
    chat_stats = chat_store.stats(cache.COMPLETIONS_NAMESPACE, _CAP)
    assert (chat_stats.entries, chat_stats.hits, chat_stats.misses) == (1, 1, 1)
    chat_store.close()

    embed_store = cache.SqliteCacheStore(tmp_path / "embeddings.db", max_bytes=_CAP)
    embed_inner = _FakeEmbedder()
    embedder = cache.CachedEmbeddingProvider(
        embed_inner, embed_store, model_build="fake:fake-embedder"
    )
    first_vector = await embedder.embed("Pierre")
    second_vector = await embedder.embed("Pierre")
    assert first_vector.embedding == second_vector.embedding == [6.0, 0.5]
    assert embed_inner.calls == 1
    embed_stats = embed_store.stats(cache.EMBEDDINGS_NAMESPACE, _CAP)
    assert (embed_stats.entries, embed_stats.hits, embed_stats.misses) == (1, 1, 1)
    embed_store.close()

    assert not [name for name in sys.modules if _is_app_module(name)]
