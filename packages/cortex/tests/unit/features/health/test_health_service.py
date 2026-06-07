# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for HealthService."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.ports.embedding import EmbeddingHealthStatus
from chaoscypher_core.services.events.health.probes import EmbeddingProbe
from chaoscypher_cortex.features.health.service import HealthService
from chaoscypher_cortex.shared.health.probes import HealthStatus


_EMBEDDING_FACTORY_PATH = "chaoscypher_core.repo_factories.embedding_factory.get_embedding_service"


def _make_healthy_embedding_provider() -> MagicMock:
    """Create a mock embedding provider that reports healthy status."""
    provider = MagicMock()
    provider.check_health = AsyncMock(
        return_value=EmbeddingHealthStatus(
            healthy=True,
            provider="local",
            model="Qwen/Qwen3-Embedding-0.6B",
            dimensions=1024,
            message="Model loaded",
            response_time_ms=5,
        )
    )
    return provider


@pytest.fixture
def mock_settings() -> MagicMock:
    """Create mock settings."""
    from chaoscypher_core.settings import OllamaInstance

    settings = MagicMock()
    settings.llm.chat_provider = "ollama"
    settings.llm.ollama_chat_model = "qwen3:30b"
    settings.llm.ollama_extraction_model = "qwen3:30b-instruct"
    settings.llm.ollama_vision_model = None
    settings.llm.ollama_instances = [
        OllamaInstance(id="default", name="Default", base_url="http://localhost:11434"),
    ]
    # primary_ollama_url is a property on real LLMSettings; mock it explicitly.
    settings.llm.primary_ollama_url = "http://localhost:11434"
    settings.embedding.model = "Qwen/Qwen3-Embedding-0.6B"
    settings.current_database = "default"
    settings.timeouts.ollama_verify_timeout = 5
    return settings


@pytest.fixture(autouse=True)
def _mock_embedding_provider() -> Generator[None]:
    """Auto-patch the embedding provider for all tests."""
    with patch(
        _EMBEDDING_FACTORY_PATH,
        return_value=_make_healthy_embedding_provider(),
    ):
        yield


@pytest.fixture
def service(mock_settings: MagicMock) -> HealthService:
    """Create health service with mocked dependencies."""
    return HealthService(
        settings=mock_settings,
        queue_client=None,
        search_service=None,
        counts_service=None,
    )


class TestOllamaHealthCheck:
    """Tests for Ollama connectivity checks."""

    @pytest.mark.asyncio
    async def test_ollama_healthy(self, service: HealthService) -> None:
        """Reports ok when Ollama responds correctly."""
        mock_resp_root = MagicMock()
        mock_resp_root.text = "Ollama is running"
        mock_resp_root.status_code = 200

        mock_resp_version = MagicMock()
        mock_resp_version.status_code = 200
        mock_resp_version.json.return_value = {"version": "0.9.1"}

        mock_resp_tags = MagicMock()
        mock_resp_tags.status_code = 200
        mock_resp_tags.json.return_value = {
            "models": [{"name": "qwen3:30b"}, {"name": "qwen3:30b-instruct"}]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                side_effect=[mock_resp_root, mock_resp_version, mock_resp_tags]
            )
            mock_client_cls.return_value = mock_client

            result = await service.check_health()

        assert result.healthy is True
        assert result.checks["ollama"].status == "ok"
        assert result.checks["chat_model"].status == "ok"
        assert result.checks["extraction_model"].status == "ok"

    @pytest.mark.asyncio
    async def test_ollama_unreachable(self, service: HealthService) -> None:
        """Reports single ollama error, no redundant model errors."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_client_cls.return_value = mock_client

            result = await service.check_health()

        assert result.healthy is False
        assert result.checks["ollama"].status == "error"
        # Model checks should be absent — not duplicating the root cause
        assert "chat_model" not in result.checks
        assert "extraction_model" not in result.checks
        assert "vision_model" not in result.checks

    @pytest.mark.asyncio
    async def test_vision_model_checked_when_configured(self, mock_settings: MagicMock) -> None:
        """Reports ok for vision model when configured and installed."""
        mock_settings.llm.ollama_vision_model = "llava:13b"
        svc = HealthService(settings=mock_settings, queue_client=None)

        mock_resp_root = MagicMock()
        mock_resp_root.text = "Ollama is running"
        mock_resp_root.status_code = 200

        mock_resp_version = MagicMock()
        mock_resp_version.status_code = 200
        mock_resp_version.json.return_value = {"version": "0.9.1"}

        mock_resp_tags = MagicMock()
        mock_resp_tags.status_code = 200
        mock_resp_tags.json.return_value = {
            "models": [
                {"name": "qwen3:30b"},
                {"name": "qwen3:30b-instruct"},
                {"name": "llava:13b"},
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                side_effect=[mock_resp_root, mock_resp_version, mock_resp_tags]
            )
            mock_client_cls.return_value = mock_client

            result = await svc.check_health()

        assert "vision_model" in result.checks
        assert result.checks["vision_model"].status == "ok"
        assert "llava:13b" in result.checks["vision_model"].message

    @pytest.mark.asyncio
    async def test_vision_model_skipped_when_not_configured(self, service: HealthService) -> None:
        """Vision model check is absent when not configured."""
        mock_resp_root = MagicMock()
        mock_resp_root.text = "Ollama is running"
        mock_resp_root.status_code = 200

        mock_resp_version = MagicMock()
        mock_resp_version.status_code = 200
        mock_resp_version.json.return_value = {"version": "0.9.1"}

        mock_resp_tags = MagicMock()
        mock_resp_tags.status_code = 200
        mock_resp_tags.json.return_value = {
            "models": [{"name": "qwen3:30b"}, {"name": "qwen3:30b-instruct"}]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                side_effect=[mock_resp_root, mock_resp_version, mock_resp_tags]
            )
            mock_client_cls.return_value = mock_client

            result = await service.check_health()

        assert "vision_model" not in result.checks

    @pytest.mark.asyncio
    async def test_chat_model_missing(self, service: HealthService) -> None:
        """Reports error when configured chat model is not installed."""
        mock_resp_root = MagicMock()
        mock_resp_root.text = "Ollama is running"
        mock_resp_root.status_code = 200

        mock_resp_version = MagicMock()
        mock_resp_version.status_code = 200
        mock_resp_version.json.return_value = {"version": "0.9.1"}

        mock_resp_tags = MagicMock()
        mock_resp_tags.status_code = 200
        mock_resp_tags.json.return_value = {
            "models": [{"name": "mistral:7b"}]  # chat model NOT in list
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                side_effect=[mock_resp_root, mock_resp_version, mock_resp_tags]
            )
            mock_client_cls.return_value = mock_client

            result = await service.check_health()

        assert result.checks["chat_model"].status == "error"
        assert "not installed" in result.checks["chat_model"].message


class TestEmbeddingHealthCheck:
    """Tests for embedding provider health checks via EmbeddingProbe."""

    @pytest.mark.asyncio
    async def test_embedding_healthy(self) -> None:
        """Reports ok when embedding provider is healthy."""
        mock_provider = MagicMock()
        mock_provider.check_health = AsyncMock(
            return_value=EmbeddingHealthStatus(
                healthy=True,
                provider="local",
                model="Qwen/Qwen3-Embedding-0.6B",
                dimensions=1024,
                message="Model loaded",
                response_time_ms=5,
            )
        )

        probe = EmbeddingProbe(health_check_fn=HealthService._get_embedding_health)
        with patch(_EMBEDDING_FACTORY_PATH, return_value=mock_provider):
            result = await probe.check()

        assert result.status == "ok"
        assert "Qwen3-Embedding-0.6B" in result.message
        assert "local" in result.message
        assert result.details is not None
        assert result.details["provider"] == "local"
        assert result.details["model"] == "Qwen/Qwen3-Embedding-0.6B"
        assert result.details["dimensions"] == 1024
        assert result.details["response_time_ms"] == 5

    @pytest.mark.asyncio
    async def test_embedding_unhealthy(self) -> None:
        """Reports error when embedding provider is unhealthy.

        Per the role-prefixed offline contract (commit e0c7f1341), the
        user-facing message is always ``"Embedding Provider {Display}
        Offline"`` regardless of the raw provider error text. The raw
        message (e.g. an httpx errno string) lands in ``details["tooltip"]``
        so the row stays short and the underlying cause is still
        inspectable on hover.
        """
        mock_provider = MagicMock()
        mock_provider.check_health = AsyncMock(
            return_value=EmbeddingHealthStatus(
                healthy=False,
                provider="ollama",
                model="nomic-embed-text",
                dimensions=0,
                message="Model not found",
            )
        )

        probe = EmbeddingProbe(health_check_fn=HealthService._get_embedding_health)
        with patch(_EMBEDDING_FACTORY_PATH, return_value=mock_provider):
            result = await probe.check()

        assert result.status == "error"
        assert result.message == "Embedding Provider Ollama Offline"
        assert result.details is not None
        assert result.details["provider"] == "ollama"
        assert result.details["tooltip"] == "Model not found"
        assert "response_time_ms" not in result.details

    @pytest.mark.asyncio
    async def test_embedding_provider_exception(self) -> None:
        """Reports error when embedding provider raises an exception.

        The original exception is logged but not exposed in the user-facing
        message — the probe returns a generic "Embedding check failed".
        """
        mock_provider = MagicMock()
        mock_provider.check_health = AsyncMock(side_effect=RuntimeError("Connection refused"))

        probe = EmbeddingProbe(health_check_fn=HealthService._get_embedding_health)
        with patch(_EMBEDDING_FACTORY_PATH, return_value=mock_provider):
            result = await probe.check()

        assert result.status == "error"
        assert result.message == "Embedding check failed"

    @pytest.mark.asyncio
    async def test_embedding_factory_exception(self) -> None:
        """Reports error when embedding factory itself fails.

        Factory errors are also generalized in the user-facing message.
        """
        probe = EmbeddingProbe(health_check_fn=HealthService._get_embedding_health)
        with patch(
            _EMBEDDING_FACTORY_PATH,
            side_effect=RuntimeError("Settings not configured"),
        ):
            result = await probe.check()

        assert result.status == "error"
        assert result.message == "Embedding check failed"

    @pytest.mark.asyncio
    async def test_embedding_unhealthy_no_message(self) -> None:
        """Role-prefixed offline message, with no tooltip when provider gave none.

        Same contract as ``test_embedding_unhealthy`` — but verifies the
        ``details["tooltip"]`` slot is *omitted* (not blank) when the
        provider returns no raw message to forward.
        """
        mock_provider = MagicMock()
        mock_provider.check_health = AsyncMock(
            return_value=EmbeddingHealthStatus(
                healthy=False,
                provider="openai",
                model="text-embedding-3-small",
                dimensions=1536,
            )
        )

        probe = EmbeddingProbe(health_check_fn=HealthService._get_embedding_health)
        with patch(_EMBEDDING_FACTORY_PATH, return_value=mock_provider):
            result = await probe.check()

        assert result.status == "error"
        assert result.message == "Embedding Provider OpenAI Offline"
        assert result.details is not None
        assert "tooltip" not in result.details


class TestInjectedProbes:
    """Tests for sibling-feature probe injection into HealthService."""

    @pytest.mark.asyncio
    async def test_injected_search_probe_folded_into_search_index_key(
        self, mock_settings: MagicMock
    ) -> None:
        """SearchHealthProbe result appears under wire-contract 'search_index' key."""

        class _FakeSearchProbe:
            name = "search"

            async def check(self) -> HealthStatus:
                return HealthStatus(
                    ok=True,
                    detail="42 docs, 42 vectors",
                    metrics={
                        "fulltext_doc_count": 42,
                        "vector_index_size": 42,
                        "vector_dimension": 384,
                    },
                )

        svc = HealthService(
            settings=mock_settings,
            queue_client=None,
            probes=[_FakeSearchProbe()],
        )

        mock_resp_root = MagicMock()
        mock_resp_root.text = "Ollama is running"
        mock_resp_root.status_code = 200
        mock_resp_version = MagicMock()
        mock_resp_version.status_code = 200
        mock_resp_version.json.return_value = {"version": "0.9.1"}
        mock_resp_tags = MagicMock()
        mock_resp_tags.status_code = 200
        mock_resp_tags.json.return_value = {
            "models": [{"name": "qwen3:30b"}, {"name": "qwen3:30b-instruct"}]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                side_effect=[mock_resp_root, mock_resp_version, mock_resp_tags]
            )
            mock_client_cls.return_value = mock_client

            result = await svc.check_health()

        # Wire-contract preservation: probe lands under 'search_index'.
        assert "search_index" in result.checks
        item = result.checks["search_index"]
        assert item.status == "ok"
        assert item.details is not None
        # Metrics translated to the keys the dashboard consumes.
        assert item.details["fulltext_count"] == 42
        assert item.details["vector_count"] == 42
        assert item.details["vector_dimension"] == 384

    @pytest.mark.asyncio
    async def test_injected_probe_exception_surfaces_as_error_item(
        self, mock_settings: MagicMock
    ) -> None:
        """Probe .check() raising is caught and becomes an error HealthCheckItem."""

        class _RaisingProbe:
            name = "search"

            async def check(self) -> HealthStatus:
                raise RuntimeError("boom")

        svc = HealthService(
            settings=mock_settings,
            queue_client=None,
            probes=[_RaisingProbe()],
        )

        mock_resp_root = MagicMock()
        mock_resp_root.text = "Ollama is running"
        mock_resp_root.status_code = 200
        mock_resp_version = MagicMock()
        mock_resp_version.status_code = 200
        mock_resp_version.json.return_value = {"version": "0.9.1"}
        mock_resp_tags = MagicMock()
        mock_resp_tags.status_code = 200
        mock_resp_tags.json.return_value = {
            "models": [{"name": "qwen3:30b"}, {"name": "qwen3:30b-instruct"}]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                side_effect=[mock_resp_root, mock_resp_version, mock_resp_tags]
            )
            mock_client_cls.return_value = mock_client

            result = await svc.check_health()

        assert "search_index" in result.checks
        assert result.checks["search_index"].status == "error"
        # The probe exception is sanitized before reaching the client; the
        # original ``str(exc)`` is intentionally not surfaced because it can
        # leak internals like DB paths or connection strings.
        assert result.checks["search_index"].message == "probe failed"

    @pytest.mark.asyncio
    async def test_empty_probes_list_preserves_inline_behavior(
        self, mock_settings: MagicMock
    ) -> None:
        """Inline SearchIndexProbe still fires when no probes are injected.

        Emits a 'search_index' key (placeholder when search_service=None).
        """
        svc = HealthService(
            settings=mock_settings,
            queue_client=None,
            probes=None,
        )

        mock_resp_root = MagicMock()
        mock_resp_root.text = "Ollama is running"
        mock_resp_root.status_code = 200
        mock_resp_version = MagicMock()
        mock_resp_version.status_code = 200
        mock_resp_version.json.return_value = {"version": "0.9.1"}
        mock_resp_tags = MagicMock()
        mock_resp_tags.status_code = 200
        mock_resp_tags.json.return_value = {
            "models": [{"name": "qwen3:30b"}, {"name": "qwen3:30b-instruct"}]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                side_effect=[mock_resp_root, mock_resp_version, mock_resp_tags]
            )
            mock_client_cls.return_value = mock_client

            result = await svc.check_health()

        # Inline probe still fires; key is present even with no injected probes.
        assert "search_index" in result.checks
