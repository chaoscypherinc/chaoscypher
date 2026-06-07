# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the Neuron embedding-model warmup gate.

Covers ``_warmup_embedding_model`` (``chaoscypher_neuron.worker``):
the function used to be scheduled unconditionally and would fetch
~600MB of Qwen weights from HuggingFace on every fresh boot, even
when the operator had not configured an embedding provider.

The fix introduced two gates:

1. **Primary** — ``settings.embedding.is_configured``: must be True
   or the warmup short-circuits with no provider construction and no
   network call.
2. **Defense in depth** — when the provider is ``local`` and the
   on-disk cache is empty, the operator must have opted in via
   ``settings.embedding.allow_model_download = True`` for the warmup
   to download eagerly. Otherwise the model is left to load lazily
   on the first real embedding request.

These tests mock ``SentenceTransformer`` so the suite never hits
HuggingFace.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_embedding_settings(
    *,
    is_configured: bool,
    provider: str = "local",
    model: str = "Qwen/Qwen3-Embedding-0.6B",
    allow_model_download: bool = False,
) -> MagicMock:
    """Build a Mock that quacks like ``EmbeddingSettings`` for the gate."""
    embedding = MagicMock()
    embedding.is_configured = is_configured
    embedding.provider = provider
    embedding.model = model
    embedding.allow_model_download = allow_model_download
    return embedding


def _make_settings_with_embedding(
    embedding: MagicMock,
    data_dir: Path,
) -> MagicMock:
    settings = MagicMock()
    settings.embedding = embedding
    settings.paths.data_dir = str(data_dir)
    # Concrete int so asyncio.wait_for in the production code can coerce it.
    settings.timeouts.llm_embedding_wait = 60
    return settings


# ============================================================================
# Primary gate: is_configured
# ============================================================================


class TestPrimaryGateUnconfigured:
    """When embedding.is_configured is False, warmup must skip entirely."""

    @pytest.mark.asyncio
    async def test_unconfigured_skips_warmup_no_embed_call(self, tmp_path: Path) -> None:
        """Unconfigured embedding -> get_embedding_service is never reached."""
        from chaoscypher_neuron.worker import _warmup_embedding_model

        settings = _make_settings_with_embedding(
            _make_embedding_settings(is_configured=False),
            data_dir=tmp_path,
        )

        with (
            patch("chaoscypher_neuron.worker.get_settings", return_value=settings),
            patch("chaoscypher_core.repo_factories.get_embedding_service") as mock_get_service,
        ):
            await _warmup_embedding_model()

        # The primary gate must skip before constructing the provider —
        # this is the heart of the "zero outbound on default install"
        # contract.
        mock_get_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_unconfigured_logs_structured_skip_event(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        """Skip event must be greppable so operators can see why."""
        from chaoscypher_neuron.worker import _warmup_embedding_model

        settings = _make_settings_with_embedding(
            _make_embedding_settings(is_configured=False),
            data_dir=tmp_path,
        )

        with (
            caplog.at_level(logging.INFO),
            patch("chaoscypher_neuron.worker.get_settings", return_value=settings),
        ):
            await _warmup_embedding_model()

        messages = [r.message for r in caplog.records]
        assert any("embedding_warmup_skipped_unconfigured" in m for m in messages), (
            f"Expected embedding_warmup_skipped_unconfigured event, got: {messages}"
        )


# ============================================================================
# Defense in depth: cache + allow_model_download
# ============================================================================


class TestSecondaryGateCacheAndOptIn:
    """When configured but cache cold and opt-in is False, still skip."""

    @pytest.mark.asyncio
    async def test_configured_local_cache_empty_no_opt_in_skips(
        self,
        tmp_path: Path,
    ) -> None:
        """Configured + cache empty + allow_model_download=False -> skip."""
        from chaoscypher_neuron.worker import _warmup_embedding_model

        # Don't create the cache dir at all — that's the "cold cache" case.
        settings = _make_settings_with_embedding(
            _make_embedding_settings(
                is_configured=True,
                provider="local",
                allow_model_download=False,
            ),
            data_dir=tmp_path,
        )

        with (
            patch("chaoscypher_neuron.worker.get_settings", return_value=settings),
            patch("chaoscypher_core.repo_factories.get_embedding_service") as mock_get_service,
        ):
            await _warmup_embedding_model()

        mock_get_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_configured_local_cache_empty_no_opt_in_logs_event(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        """Skip path emits the no-cache event for operator visibility."""
        from chaoscypher_neuron.worker import _warmup_embedding_model

        settings = _make_settings_with_embedding(
            _make_embedding_settings(
                is_configured=True,
                provider="local",
                allow_model_download=False,
            ),
            data_dir=tmp_path,
        )

        with (
            caplog.at_level(logging.INFO),
            patch("chaoscypher_neuron.worker.get_settings", return_value=settings),
        ):
            await _warmup_embedding_model()

        messages = [r.message for r in caplog.records]
        assert any("embedding_warmup_skipped_no_cache_no_opt_in" in m for m in messages), (
            f"Expected no_cache_no_opt_in event, got: {messages}"
        )

    @pytest.mark.asyncio
    async def test_configured_local_cache_empty_opt_in_proceeds(
        self,
        tmp_path: Path,
    ) -> None:
        """Configured + cache empty + opt-in True -> warmup runs (HF mocked)."""
        from chaoscypher_neuron.worker import _warmup_embedding_model

        settings = _make_settings_with_embedding(
            _make_embedding_settings(
                is_configured=True,
                provider="local",
                allow_model_download=True,
            ),
            data_dir=tmp_path,
        )

        mock_service = MagicMock()
        mock_service.embed = AsyncMock(return_value=MagicMock(embedding=[0.0] * 16))
        mock_service.model_name = "Qwen/Qwen3-Embedding-0.6B"

        with (
            patch("chaoscypher_neuron.worker.get_settings", return_value=settings),
            # SentenceTransformer must never be touched even when warmup
            # proceeds — get_embedding_service builds the provider which
            # would import sentence_transformers. We swap the factory wholesale.
            patch(
                "chaoscypher_core.repo_factories.get_embedding_service",
                return_value=mock_service,
            ),
            patch(
                "sentence_transformers.SentenceTransformer",
                side_effect=AssertionError("SentenceTransformer must not be constructed in tests"),
            ),
        ):
            await _warmup_embedding_model()

        mock_service.embed.assert_awaited_once_with("warmup")


# ============================================================================
# Happy path: cache populated
# ============================================================================


class TestCachePopulatedProceeds:
    """Existing happy path: configured + cache populated -> warmup runs."""

    @pytest.mark.asyncio
    async def test_configured_local_cache_populated_proceeds(
        self,
        tmp_path: Path,
    ) -> None:
        """When cache has a model dir, warmup proceeds regardless of opt-in."""
        from chaoscypher_neuron.worker import _warmup_embedding_model

        # Materialize what SentenceTransformer + huggingface_hub would create
        # after a successful snapshot_download: a per-repo dir under the
        # cache root. Existence is what the gate inspects, not contents.
        cache_dir = tmp_path / "models" / "embeddings"
        (cache_dir / "models--Qwen--Qwen3-Embedding-0.6B").mkdir(parents=True)

        settings = _make_settings_with_embedding(
            _make_embedding_settings(
                is_configured=True,
                provider="local",
                # Cache already populated => opt-in is irrelevant.
                allow_model_download=False,
            ),
            data_dir=tmp_path,
        )

        mock_service = MagicMock()
        mock_service.embed = AsyncMock(return_value=MagicMock(embedding=[0.0] * 16))
        mock_service.model_name = "Qwen/Qwen3-Embedding-0.6B"

        with (
            patch("chaoscypher_neuron.worker.get_settings", return_value=settings),
            patch(
                "chaoscypher_core.repo_factories.get_embedding_service",
                return_value=mock_service,
            ),
            patch(
                "sentence_transformers.SentenceTransformer",
                side_effect=AssertionError("SentenceTransformer must not be constructed in tests"),
            ),
        ):
            await _warmup_embedding_model()

        mock_service.embed.assert_awaited_once_with("warmup")

    @pytest.mark.asyncio
    async def test_configured_non_local_provider_proceeds(
        self,
        tmp_path: Path,
    ) -> None:
        """Non-local providers (ollama / openai / gemini) skip the cache gate."""
        from chaoscypher_neuron.worker import _warmup_embedding_model

        settings = _make_settings_with_embedding(
            _make_embedding_settings(
                is_configured=True,
                provider="ollama",
                allow_model_download=False,
            ),
            data_dir=tmp_path,
        )

        mock_service = MagicMock()
        mock_service.embed = AsyncMock(return_value=MagicMock(embedding=[0.0] * 16))
        mock_service.model_name = "qwen3-embedding:0.6b"

        with (
            patch("chaoscypher_neuron.worker.get_settings", return_value=settings),
            patch(
                "chaoscypher_core.repo_factories.get_embedding_service",
                return_value=mock_service,
            ),
        ):
            await _warmup_embedding_model()

        mock_service.embed.assert_awaited_once_with("warmup")


class TestWarmupTimeout:
    """A hung embed("warmup") must not keep the provider connection forever."""

    @pytest.mark.asyncio
    async def test_warmup_aborts_on_timeout(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        """An embed() that exceeds llm_embedding_wait raises TimeoutError and logs."""
        import asyncio

        from chaoscypher_neuron.worker import _warmup_embedding_model

        settings = _make_settings_with_embedding(
            _make_embedding_settings(
                is_configured=True,
                provider="ollama",
            ),
            data_dir=tmp_path,
        )
        settings.timeouts.llm_embedding_wait = 0  # immediate timeout

        async def never_returns(_text: str) -> object:
            await asyncio.sleep(10)
            return MagicMock()

        mock_service = MagicMock()
        mock_service.embed = AsyncMock(side_effect=never_returns)
        mock_service.model_name = "qwen3-embedding:0.6b"

        with (
            caplog.at_level(logging.WARNING),
            patch("chaoscypher_neuron.worker.get_settings", return_value=settings),
            patch(
                "chaoscypher_core.repo_factories.get_embedding_service",
                return_value=mock_service,
            ),
        ):
            await _warmup_embedding_model()

        messages = [r.message for r in caplog.records]
        assert any("embedding_model_warmup_timeout" in m for m in messages), (
            f"Expected warmup_timeout event, got: {messages}"
        )


# ============================================================================
# Cache helper unit tests
# ============================================================================


class TestEmbeddingCacheHasModel:
    """Direct tests of ``_embedding_cache_has_model``."""

    def test_missing_dir_returns_false(self, tmp_path: Path) -> None:
        from chaoscypher_neuron.worker import _embedding_cache_has_model

        assert _embedding_cache_has_model(tmp_path / "does" / "not" / "exist") is False

    def test_empty_dir_returns_false(self, tmp_path: Path) -> None:
        from chaoscypher_neuron.worker import _embedding_cache_has_model

        empty = tmp_path / "empty"
        empty.mkdir()
        assert _embedding_cache_has_model(empty) is False

    def test_dir_with_only_files_returns_false(self, tmp_path: Path) -> None:
        """Bare files at the cache root don't count — only model subdirs do."""
        from chaoscypher_neuron.worker import _embedding_cache_has_model

        (tmp_path / "stray-file.txt").write_text("not a model")
        assert _embedding_cache_has_model(tmp_path) is False

    def test_dir_with_model_subdir_returns_true(self, tmp_path: Path) -> None:
        from chaoscypher_neuron.worker import _embedding_cache_has_model

        (tmp_path / "models--Qwen--Qwen3-Embedding-0.6B").mkdir()
        assert _embedding_cache_has_model(tmp_path) is True
