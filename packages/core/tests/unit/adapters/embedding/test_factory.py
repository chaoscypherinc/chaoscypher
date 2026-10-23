# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for embedding provider factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.adapters.embedding.factory import (
    create_embedding_provider,
    validate_embedding_model,
)
from chaoscypher_core.adapters.embedding.gemini_provider import GeminiEmbeddingProvider
from chaoscypher_core.adapters.embedding.local_provider import LocalEmbeddingProvider
from chaoscypher_core.adapters.embedding.models import ModelValidationResult
from chaoscypher_core.adapters.embedding.ollama_provider import OllamaEmbeddingProvider
from chaoscypher_core.adapters.embedding.openai_provider import OpenAIEmbeddingProvider
from chaoscypher_core.cache.embedding import CachedEmbeddingProvider


def _make_settings(
    provider: str = "local",
    model: str = "test-model",
    vector_dimensions: int = 1024,
    api_key: str | None = None,
    api_base: str | None = None,
    data_dir: str = "/tmp/test-data",
    ollama_base_url: str = "http://localhost:11434",
    ollama_instances: list | None = None,
    ollama_instance_id: str = "default",
    cache_enabled: bool = True,
) -> MagicMock:
    """Build a mock EngineSettings with the required nested attributes.

    The legacy ``ollama_base_url`` parameter (kept for test brevity) is
    materialized into a single seeded Ollama instance, mirroring the real
    LLMSettings default behaviour.
    """
    from chaoscypher_core.settings import CacheSettings, CacheStoreSettings, OllamaInstance

    if ollama_instances is None:
        ollama_instances = [
            OllamaInstance(id="default", name="Default", base_url=ollama_base_url),
        ]

    from pydantic import SecretStr

    settings = MagicMock()
    settings.cache = CacheSettings(
        embeddings=CacheStoreSettings(enabled=cache_enabled),
    )
    settings.database.connection_timeout_secs = 5
    settings.embedding.provider = provider
    settings.embedding.model = model
    settings.embedding.api_key = SecretStr(api_key) if api_key else None
    settings.embedding.api_base = api_base
    settings.embedding.ollama_instance_id = ollama_instance_id
    settings.search.vector_dimensions = vector_dimensions
    settings.paths.data_dir = data_dir
    settings.llm.ollama_instances = ollama_instances
    # primary_ollama_url is a property on real LLMSettings; mock it explicitly
    # so the embedding factory's fallback path resolves correctly.
    settings.llm.primary_ollama_url = (
        ollama_instances[0].base_url if ollama_instances else ollama_base_url
    )
    return settings


def _inner(provider: Any) -> Any:
    """The provider the factory built, behind the embedding cache it wraps by default."""
    assert isinstance(provider, CachedEmbeddingProvider)
    return provider.inner


class TestCreateEmbeddingProvider:
    """Tests for create_embedding_provider factory function."""

    def test_create_local_provider(self) -> None:
        """create_embedding_provider returns LocalEmbeddingProvider for 'local'."""
        settings = _make_settings(provider="local", model="Qwen/Qwen3-Embedding-0.6B")
        provider = create_embedding_provider(settings)

        assert isinstance(_inner(provider), LocalEmbeddingProvider)
        assert provider.model_name == "Qwen/Qwen3-Embedding-0.6B"
        assert provider.vector_dimensions == 1024
        assert provider.cache_dir == Path("/tmp/test-data/models/embeddings")

    def test_create_ollama_provider(self) -> None:
        """create_embedding_provider returns OllamaEmbeddingProvider for 'ollama'."""
        settings = _make_settings(
            provider="ollama",
            model="qwen3-embedding:0.6b",
            ollama_base_url="http://my-ollama:11434",
        )
        provider = create_embedding_provider(settings)

        assert isinstance(_inner(provider), OllamaEmbeddingProvider)
        assert provider.model_name == "qwen3-embedding:0.6b"
        assert provider.vector_dimensions == 1024
        assert provider.base_url == "http://my-ollama:11434"

    def test_create_ollama_provider_with_api_base_override(self) -> None:
        """api_base in embedding settings overrides ollama_base_url."""
        settings = _make_settings(
            provider="ollama",
            model="qwen3-embedding:0.6b",
            api_base="http://custom-ollama:9999",
            ollama_base_url="http://default-ollama:11434",
        )
        provider = create_embedding_provider(settings)

        assert isinstance(_inner(provider), OllamaEmbeddingProvider)
        assert provider.base_url == "http://custom-ollama:9999"

    def test_create_ollama_provider_with_instance_match(self) -> None:
        """Matching ollama instance provides base_url when api_base is None."""
        from chaoscypher_core.settings import OllamaInstance

        instances = [
            OllamaInstance(id="gpu-box", name="GPU Box", base_url="http://gpu-box:11434"),
            OllamaInstance(id="default", name="Default", base_url="http://instance-default:11434"),
        ]
        settings = _make_settings(
            provider="ollama",
            model="qwen3-embedding:0.6b",
            ollama_instances=instances,
            ollama_instance_id="gpu-box",
        )
        provider = create_embedding_provider(settings)

        assert isinstance(_inner(provider), OllamaEmbeddingProvider)
        assert provider.base_url == "http://gpu-box:11434"

    def test_create_openai_provider(self) -> None:
        """create_embedding_provider returns OpenAIEmbeddingProvider for 'openai'."""
        settings = _make_settings(
            provider="openai",
            model="text-embedding-3-large",
            api_key="sk-test-key",
        )
        provider = create_embedding_provider(settings)

        assert isinstance(_inner(provider), OpenAIEmbeddingProvider)
        assert provider.model_name == "text-embedding-3-large"
        assert provider.vector_dimensions == 1024
        assert provider.api_key == "sk-test-key"

    def test_create_openai_provider_with_custom_base(self) -> None:
        """OpenAI provider uses api_base override when provided."""
        settings = _make_settings(
            provider="openai",
            model="text-embedding-3-large",
            api_key="sk-test-key",
            api_base="https://custom.openai.proxy",
        )
        provider = create_embedding_provider(settings)

        assert isinstance(_inner(provider), OpenAIEmbeddingProvider)
        assert provider.api_base == "https://custom.openai.proxy"

    def test_create_gemini_provider(self) -> None:
        """create_embedding_provider returns GeminiEmbeddingProvider for 'gemini'."""
        settings = _make_settings(
            provider="gemini",
            model="gemini-embedding-001",
            api_key="AIza-test-key",
        )
        provider = create_embedding_provider(settings)

        assert isinstance(_inner(provider), GeminiEmbeddingProvider)
        assert provider.model_name == "gemini-embedding-001"
        assert provider.vector_dimensions == 1024
        assert provider.api_key == "AIza-test-key"

    def test_unknown_provider_raises_error(self) -> None:
        """ValueError is raised for an unknown provider name."""
        settings = _make_settings(provider="invalid")

        with pytest.raises(ValueError, match="Unknown embedding provider: invalid"):
            create_embedding_provider(settings)

    def test_cloud_provider_missing_api_key_openai(self) -> None:
        """ValueError is raised when OpenAI provider has no API key."""
        settings = _make_settings(provider="openai", api_key=None)

        with pytest.raises(ValueError, match="API key required for openai"):
            create_embedding_provider(settings)

    def test_cloud_provider_missing_api_key_gemini(self) -> None:
        """ValueError is raised when Gemini provider has no API key."""
        settings = _make_settings(provider="gemini", api_key=None)

        with pytest.raises(ValueError, match="API key required for gemini"):
            create_embedding_provider(settings)


class TestValidateEmbeddingModel:
    """Tests for validate_embedding_model helper function."""

    def test_validate_curated_model(self) -> None:
        """Curated model returns valid=True with known native_dimensions."""
        settings = _make_settings()
        result = validate_embedding_model("local", "Qwen/Qwen3-Embedding-0.6B", settings)

        assert isinstance(result, ModelValidationResult)
        assert result.valid is True
        assert result.model == "Qwen/Qwen3-Embedding-0.6B"
        assert result.native_dimensions == 1024

    def test_validate_curated_cloud_model(self) -> None:
        """Cloud model from registry returns valid=True with known dimensions."""
        settings = _make_settings()
        result = validate_embedding_model("openai", "text-embedding-3-large", settings)

        assert result.valid is True
        assert result.native_dimensions == 3072

    def test_validate_unknown_model(self) -> None:
        """Unknown model returns valid=True with native_dimensions=None."""
        settings = _make_settings()
        result = validate_embedding_model("local", "some-custom/model", settings)

        assert isinstance(result, ModelValidationResult)
        assert result.valid is True
        assert result.model == "some-custom/model"
        assert result.native_dimensions is None


class TestEmbeddingCacheWiring:
    """The factory wraps its provider in the embedding cache when the section enables it."""

    def test_the_factory_wraps_when_enabled_and_not_when_disabled(self, tmp_path: Path) -> None:
        from chaoscypher_core.settings import CacheSettings, CacheStoreSettings, EngineSettings

        enabled = EngineSettings(
            paths={"data_dir": str(tmp_path)},
            embedding={"provider": "ollama", "model": "qwen3-embedding:0.6b"},
        )
        disabled = enabled.model_copy(
            update={"cache": CacheSettings(embeddings=CacheStoreSettings(enabled=False))}
        )
        wrapped = create_embedding_provider(enabled)
        assert isinstance(wrapped, CachedEmbeddingProvider)
        assert isinstance(wrapped.inner, OllamaEmbeddingProvider)
        bare = create_embedding_provider(disabled)
        assert isinstance(bare, OllamaEmbeddingProvider)
        assert not (tmp_path / "cache").exists()  # building a provider opens nothing

    def test_a_completions_switch_does_not_turn_the_embedding_cache_off(
        self, tmp_path: Path
    ) -> None:
        from chaoscypher_core.settings import CacheSettings, CacheStoreSettings, EngineSettings

        settings = EngineSettings(
            paths={"data_dir": str(tmp_path)},
            cache=CacheSettings(completions=CacheStoreSettings(enabled=False)),
        )
        assert isinstance(create_embedding_provider(settings), CachedEmbeddingProvider)

    @pytest.mark.asyncio
    async def test_a_second_embed_through_the_real_factory_never_reaches_the_server(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        llm_cache_in_tmp: Path,
    ) -> None:
        """The real factory and the real Ollama provider over a fake HTTP layer."""
        import httpx

        from chaoscypher_core.adapters.llm.providers import ollama_provider as llm_ollama
        from chaoscypher_core.cache.embedding import EMBEDDINGS_NAMESPACE
        from chaoscypher_core.cache.wiring import close_stores, open_store
        from chaoscypher_core.settings import EngineSettings

        builds: list[str] = []

        async def fake_build(base_url: str, model: str, **_: Any) -> dict[str, Any]:
            builds.append(model)
            return {"digest": "sha256:abc", "server_version": "0.12.1"}

        monkeypatch.setattr(llm_ollama, "ollama_model_build", fake_build)
        requests: list[Any] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"embeddings": [[0.6, 0.8, 0.0, 0.0]]})

        settings = EngineSettings(
            paths={"data_dir": str(tmp_path)},
            embedding={"provider": "ollama", "model": "qwen3-embedding:0.6b"},
            search={"vector_dimensions": 4},
        )
        try:
            provider = create_embedding_provider(settings)
            assert isinstance(provider, CachedEmbeddingProvider)
            provider.inner._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            first = await provider.embed("Pierre kept the accounts.")
            second = await provider.embed("Pierre kept the accounts.")
            assert first.embedding == second.embedding
            assert len(requests) == 1 and builds == ["qwen3-embedding:0.6b"]
            # the autouse fixture keeps every store in its own directory, never the data dir
            assert (llm_cache_in_tmp / "embeddings.db").exists()
            assert not (tmp_path / "cache" / "llm" / "embeddings.db").exists()
            stats = open_store(settings, "embeddings").stats(EMBEDDINGS_NAMESPACE, 1)
            assert (stats.hits, stats.misses) == (1, 1)
            await provider.inner.aclose()
        finally:
            close_stores()

    @pytest.mark.asyncio
    async def test_an_ollama_server_without_a_digest_bypasses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from chaoscypher_core.adapters.llm.providers import ollama_provider as llm_ollama
        from chaoscypher_core.cache.wiring import close_stores
        from chaoscypher_core.settings import EngineSettings

        async def no_digest(base_url: str, model: str, **_: Any) -> dict[str, Any]:
            return {"digest": None, "server_version": None}

        monkeypatch.setattr(llm_ollama, "ollama_model_build", no_digest)
        settings = EngineSettings(
            paths={"data_dir": str(tmp_path)},
            embedding={"provider": "ollama", "model": "m"},
        )
        try:
            provider = create_embedding_provider(settings)
            assert isinstance(provider, CachedEmbeddingProvider)
            assert await provider._build.get() is None
        finally:
            close_stores()
