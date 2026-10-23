# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The Ollama embedder's model build comes from the completion cache's one digest resolver."""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.adapters.embedding import factory
from chaoscypher_core.cache import wiring
from chaoscypher_core.cache.keys import model_build_id


@pytest.mark.asyncio
async def test_ollama_embedding_build_goes_through_the_shared_digest_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chaoscypher_core.adapters.llm.providers import ollama_provider

    reports: list[tuple[str, str]] = []

    async def fake_build(base_url: str, model: str, **_: Any) -> dict[str, Any]:
        reports.append((base_url, model))
        return {"digest": "abc123", "server_version": "0.12.1"}

    resolvers: list[tuple[str, str]] = []
    real = wiring.digest_build_resolver

    def spy(provider_type: str, base_url: str, report: Any) -> Any:
        resolvers.append((provider_type, base_url))
        return real(provider_type, base_url, report)

    monkeypatch.setattr(ollama_provider, "ollama_model_build", fake_build)
    monkeypatch.setattr(factory, "digest_build_resolver", spy)

    resolve = factory.ollama_embedding_build("http://gpu:11434", "qwen3-embedding:4b")
    assert resolvers == [("ollama", "http://gpu:11434")]  # built at wrap time, asks nothing yet
    assert reports == []
    assert await resolve() == model_build_id(
        "ollama", "qwen3-embedding:4b", digest="abc123", server_version="0.12.1"
    )
    assert reports == [("http://gpu:11434", "qwen3-embedding:4b")]


@pytest.mark.asyncio
async def test_a_server_without_a_digest_resolves_no_build(monkeypatch: pytest.MonkeyPatch) -> None:
    from chaoscypher_core.adapters.llm.providers import ollama_provider

    async def no_digest(base_url: str, model: str, **_: Any) -> dict[str, Any]:
        return {"digest": None, "server_version": "0.12.1"}

    monkeypatch.setattr(ollama_provider, "ollama_model_build", no_digest)
    assert await factory.ollama_embedding_build("http://gpu:11434", "m")() is None
