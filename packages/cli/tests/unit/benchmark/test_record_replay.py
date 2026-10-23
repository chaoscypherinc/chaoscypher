# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Record and replay: the chat stage's call sites, the run's caches reaching every provider, replay."""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_cli.benchmark.chat_dataset import GraphRAGChatDataset
from chaoscypher_cli.benchmark.models import ModelConfig
from chaoscypher_cli.benchmark.queries import LabeledQuery, LabeledQuerySet
from chaoscypher_cli.benchmark.runner import run_benchmark
from chaoscypher_core.cache.chat import COMPLETIONS_NAMESPACE, CachedChatProvider
from chaoscypher_core.cache.sqlite_store import SqliteCacheStore
from chaoscypher_core.exceptions import ValidationError


_CAP = 1 << 24


class _Hosted:
    """A hosted chat provider at temperature 0 (cacheable without a seed) that counts its calls."""

    def __init__(self, provider: str, model: str, content: str) -> None:
        self.config = {"chat_provider": provider, "ai_temperature": 0.0}
        self.chat_model = model
        self.content = content
        self.calls = 0

    async def chat(self, messages: Any, **_: Any) -> dict[str, Any]:
        self.calls += 1
        return {"content": self.content, "tool_calls": None, "finish_reason": "stop"}


def _queries() -> LabeledQuerySet:
    return LabeledQuerySet(
        version="1.0",
        queries=[
            LabeledQuery(
                id="q1",
                band="factual_single_hop",
                question="Who kept the accounts?",
                gold_entities=["Pierre"],
                gold_answer="Pierre.",
            ),
            LabeledQuery(
                id="q2", band="out_of_scope", question="Who won in 1950?", expect_refusal=True
            ),
        ],
    )


@pytest.mark.asyncio
async def test_the_chat_stage_records_its_hits_under_the_chat_and_judge_sites(
    tmp_path: Path,
) -> None:
    """Through the real runner: the candidate's calls count as ``chat``, the judge's as ``judge``."""
    store = SqliteCacheStore(tmp_path / "completions.db", max_bytes=_CAP)
    candidate = CachedChatProvider(_Hosted("anthropic", "claude-x", "Pierre."), store)
    judge = CachedChatProvider(_Hosted("openai", "gpt-judge", "5"), store)

    async def chat(_model: Any, question: str, _retrieved: Any, _ctx: Any) -> str:
        answer = await candidate.chat([{"role": "user", "content": question}])
        return str(answer["content"])

    async def judge_call(_judge: Any, prompt: str) -> str:
        answer = await judge.chat([{"role": "user", "content": prompt}])
        return str(answer["content"])

    @asynccontextmanager
    async def indexed_graph(*, embedder: Any = None) -> Any:
        yield MagicMock()

    provider = MagicMock()
    provider.indexed_graph = indexed_graph
    dataset = GraphRAGChatDataset(
        id="demo",
        version="1.0",
        corpus_id="demo",
        queries=_queries(),
        graph_provider=provider,
        graphrag_search=_retrieved,
        chat=chat,
        judge=ModelConfig(provider="openai", model="gpt-judge", label="J"),
        judge_call=judge_call,
    )
    model = ModelConfig(provider="anthropic", model="claude-x", label="C")
    for _ in range(2):
        rows = await run_benchmark([model], [dataset])
        assert rows[0].success, rows[0].error
    sites = store.stats(COMPLETIONS_NAMESPACE, _CAP).by_call_site
    assert sites["chat"]["misses"] == 2 and sites["chat"]["hits"] == 2
    # four judge prompts a run: faithfulness and correctness for q1, faithfulness and refusal
    # for q2; the two faithfulness prompts are one (same answer, same context), so three keys
    assert sites["judge"]["misses"] == 3 and sites["judge"]["hits"] == 5
    assert "other" not in sites
    # informational: the row says which caches it ran with (no run settings on a stub context)
    assert rows[0].extras["cache"] == {
        "completions_enabled": True,
        "embeddings_enabled": True,
        "replay": False,
    }


async def _retrieved(_question: str, _ctx: Any) -> dict[str, Any]:
    return {"entities": [{"id": "e1", "name": "Pierre"}]}


def test_candidate_settings_carry_the_runs_caches_paths_and_database(tmp_path: Path) -> None:
    """Until the record/replay work the chat and judge providers saw the default cache section."""
    from chaoscypher_cli.benchmark.orchestrator import RUN_SECTIONS, candidate_engine_settings
    from chaoscypher_core.settings import EngineSettings, LLMSettings

    run = EngineSettings(
        paths={"data_dir": str(tmp_path)},
        cache={"dir": str(tmp_path / "c"), "completions": {"require_hit": True}},
        database={"connection_timeout_secs": 7},
    )
    built = candidate_engine_settings(LLMSettings(chat_provider="anthropic"), run)
    for name in RUN_SECTIONS:
        assert getattr(built, name) == getattr(run, name)
        assert getattr(built, name) is not getattr(run, name)  # a copy
    assert built.llm.chat_provider == "anthropic"


class _Ollama:
    """An Ollama chat provider the factory's own build resolver reads (``config["base_url"]``)."""

    def __init__(self) -> None:
        self.config = {
            "chat_provider": "ollama",
            "base_url": "http://gpu:11434",
            "seed": 42,
            "ai_temperature": 0.0,
        }
        self.chat_model = "gemma4:31b"
        self.calls = 0

    async def chat(self, messages: Any, **_: Any) -> dict[str, Any]:
        self.calls += 1
        return {"content": f"answer {self.calls}", "tool_calls": None, "finish_reason": "stop"}


@pytest.mark.asyncio
async def test_a_replay_serves_the_recorded_run_with_no_model_call_and_no_digest_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Record through the real factory resolver, then replay under the manifest's build."""
    from chaoscypher_cli.benchmark.orchestrator import candidate_engine_settings, replaying
    from chaoscypher_core.adapters.llm.factory import chat_build_resolver
    from chaoscypher_core.adapters.llm.providers import ollama_provider
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.cache.keys import model_build_id
    from chaoscypher_core.cache.wiring import close_stores, wrap_chat
    from chaoscypher_core.settings import LLMSettings

    lookups: list[str] = []

    async def digest(_base_url: str, model: str, **_: Any) -> dict[str, Any]:
        lookups.append(model)
        return {"digest": "abc123", "server_version": "0.12.1"}

    monkeypatch.setattr(ollama_provider, "ollama_model_build", digest)
    question = [{"role": "user", "content": "Who kept the accounts?"}]
    settings = get_settings()
    try:
        recorder = _Ollama()
        recorded = wrap_chat(
            recorder,
            candidate_engine_settings(LLMSettings(), settings),
            build_resolver=chat_build_resolver(recorder),
        )
        first = await recorded.chat(question)
        assert recorder.calls == 1 and lookups == ["gemma4:31b"]
        build = model_build_id("ollama", "gemma4:31b", digest="abc123", server_version="0.12.1")

        lookups.clear()
        before = copy.deepcopy(settings.cache)
        inner = _Ollama()
        with replaying(settings, {("ollama", "gemma4:31b"): build}):
            replay = wrap_chat(
                inner,
                candidate_engine_settings(LLMSettings(), settings),
                build_resolver=chat_build_resolver(inner),
            )
            assert await replay.chat(question) == first
            with pytest.raises(ValidationError, match="replay miss"):
                await replay.chat([{"role": "user", "content": "A question never recorded"}])
        assert inner.calls == 0 and lookups == []
        assert settings.cache == before  # require_hit restored after the run
    finally:
        close_stores()


def test_a_replay_refuses_a_completion_cache_that_is_off_and_ignores_the_embedding_one() -> None:
    """Only completions are replayed: packs ship no embeddings, the embedder recomputes them."""
    from chaoscypher_cli.benchmark.orchestrator import replaying
    from chaoscypher_core.settings import CacheSettings, CacheStoreSettings, EngineSettings

    settings = EngineSettings(cache=CacheSettings(completions=CacheStoreSettings(enabled=False)))
    with pytest.raises(ValidationError, match=r"cache\.completions is off"), replaying(settings):
        pass
    assert settings.cache.completions.require_hit is False

    settings = EngineSettings(cache=CacheSettings(embeddings=CacheStoreSettings(enabled=False)))
    with replaying(settings):
        assert settings.cache.completions.require_hit is True
        assert settings.cache.embeddings.require_hit is False
    assert settings.cache.completions.require_hit is False


def test_replay_builds_come_from_the_packs_manifests() -> None:
    from types import SimpleNamespace

    from chaoscypher_cli.benchmark.orchestrator import replay_builds

    def pack(name: str, build: str | None, extractor: str = "ollama/gemma4:31b") -> Any:
        return SimpleNamespace(name=name, extractor=extractor, model_build=build)

    assert replay_builds([pack("v3", "ollama:gemma4:31b:abc")]) == {
        ("ollama", "gemma4:31b"): "ollama:gemma4:31b:abc"
    }
    with pytest.raises(ValidationError, match="ships no LLM store"):
        replay_builds([pack("v1", None)])
    with pytest.raises(ValidationError, match="different builds"):
        replay_builds([pack("v2", "ollama:gemma4:31b:old"), pack("v3", "ollama:gemma4:31b:abc")])
