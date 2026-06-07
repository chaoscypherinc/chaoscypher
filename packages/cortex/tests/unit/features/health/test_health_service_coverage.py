# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage-focused unit tests for HealthService.

Targets branches the existing ``test_health_service.py`` leaves uncovered:
the minimal (unauthenticated) health path, the cloud-provider registration
branch, the cache-hit short-circuit, the synthetic unconfigured-extraction
warning, and the queue-dependent worker/error-rate/database factory helpers.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.ports.embedding import EmbeddingHealthStatus
from chaoscypher_cortex.features.health.models import (
    HealthCheckItem,
    HealthCheckResponse,
)
from chaoscypher_cortex.features.health.service import HealthService


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
def ollama_settings() -> MagicMock:
    """Mock settings configured for the ollama provider."""
    settings = MagicMock()
    settings.llm.chat_provider = "ollama"
    settings.llm.ollama_chat_model = "qwen3:30b"
    settings.llm.ollama_extraction_model = "qwen3:30b-instruct"
    settings.llm.ollama_vision_model = None
    settings.llm.primary_ollama_url = "http://localhost:11434"
    settings.current_database = "default"
    settings.timeouts.ollama_verify_timeout = 5
    return settings


@pytest.fixture
def cloud_settings() -> MagicMock:
    """Mock settings configured for a cloud (openai) provider."""
    settings = MagicMock()
    settings.llm.chat_provider = "openai"
    settings.llm.openai_api_key = "sk-test-key"
    settings.current_database = "default"
    return settings


@pytest.fixture(autouse=True)
def _mock_embedding_provider() -> Generator[None]:
    """Auto-patch the embedding provider for all tests."""
    with patch(_EMBEDDING_FACTORY_PATH, return_value=_make_healthy_embedding_provider()):
        yield


# ---------------------------------------------------------------------------
# _minimal_health — unauthenticated callers (lines ~343-360)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minimal_health_no_cache_returns_placeholder_ok(
    ollama_settings: MagicMock,
) -> None:
    """With no cached response, detailed=False returns a healthy placeholder."""
    service = HealthService(settings=ollama_settings, queue_client=None)

    result = await service.check_health(detailed=False)

    assert isinstance(result, HealthCheckResponse)
    assert result.healthy is True
    assert result.status == "ok"
    assert result.checks is None  # no subsystem details leaked


@pytest.mark.asyncio
async def test_minimal_health_reflects_cached_degraded(
    ollama_settings: MagicMock,
) -> None:
    """Minimal health mirrors a cached degraded response's healthy flag."""
    service = HealthService(settings=ollama_settings, queue_client=None)
    # Seed the cache with an unhealthy full response.
    service._cache = (
        time.monotonic(),
        HealthCheckResponse(healthy=False, status="degraded", checks={}),
    )

    result = await service.check_health(detailed=False)

    assert result.healthy is False
    assert result.status == "degraded"
    assert result.checks is None


# ---------------------------------------------------------------------------
# Cloud-provider registration branch (lines ~155-164)
# ---------------------------------------------------------------------------


def test_cloud_provider_registers_provider_probe(cloud_settings: MagicMock) -> None:
    """A non-ollama provider registers a single CloudProviderProbe as 'provider'."""
    service = HealthService(settings=cloud_settings, queue_client=None)

    probe_names = set(service.registry.probes.keys())

    assert "provider" in probe_names
    # Ollama-only probes must be absent for a cloud provider.
    assert "ollama" not in probe_names
    assert "chat_model" not in probe_names


# ---------------------------------------------------------------------------
# Cache-hit short-circuit (_full_health TTL branch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_health_returns_cached_within_ttl(ollama_settings: MagicMock) -> None:
    """A fresh cached full response is returned without re-running probes."""
    service = HealthService(settings=ollama_settings, queue_client=None)
    cached = HealthCheckResponse(
        healthy=True,
        status="ok",
        checks={"ollama": HealthCheckItem(status="ok", message="cached")},
    )
    service._cache = (time.monotonic(), cached)

    with patch.object(service._registry, "check_all", new=AsyncMock()) as mock_check_all:
        result = await service.check_health(detailed=True)

    assert result is cached
    mock_check_all.assert_not_called()


# ---------------------------------------------------------------------------
# Synthetic unconfigured-extraction-model warning (lines ~409-418)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unconfigured_extraction_model_injects_warning(
    ollama_settings: MagicMock,
) -> None:
    """When Ollama is ok but no extraction model is set, a warning is injected."""
    ollama_settings.llm.ollama_extraction_model = None
    service = HealthService(settings=ollama_settings, queue_client=None)

    mock_resp_root = MagicMock()
    mock_resp_root.text = "Ollama is running"
    mock_resp_root.status_code = 200
    mock_resp_version = MagicMock()
    mock_resp_version.status_code = 200
    mock_resp_version.json.return_value = {"version": "0.9.1"}
    mock_resp_tags = MagicMock()
    mock_resp_tags.status_code = 200
    mock_resp_tags.json.return_value = {"models": [{"name": "qwen3:30b"}]}

    with patch("httpx.AsyncClient") as mock_client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(side_effect=[mock_resp_root, mock_resp_version, mock_resp_tags])
        mock_client_cls.return_value = client

        result = await service.check_health(detailed=True)

    assert "extraction_model" in result.checks
    assert result.checks["extraction_model"].status == "warning"
    assert "Not configured" in result.checks["extraction_model"].message


# ---------------------------------------------------------------------------
# Queue-dependent factory helpers (lines ~255-315)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_make_worker_health_fn_reads_health_hash() -> None:
    """The worker health fn awaits hgetall on the queue's health key."""
    queue_client = MagicMock()
    queue_client.hgetall = AsyncMock(return_value={"status": "alive", "concurrency": "4"})

    fn = HealthService._make_worker_health_fn(queue_client, "llm")
    result = await fn()

    assert result == {"status": "alive", "concurrency": "4"}
    queue_client.hgetall.assert_awaited_once_with("queue:llm:health")


@pytest.mark.asyncio
async def test_make_error_rate_fn_sums_completed_and_failed() -> None:
    """The error-rate fn sums completed+failed counters across both queues."""
    queue_client = MagicMock()
    # get is called for completed then failed, per queue (llm, operations).
    queue_client.get = AsyncMock(side_effect=["10", "2", "5", "3"])

    fn = HealthService._make_error_rate_fn(queue_client)
    result = await fn()

    # total = (10+2) + (5+3) = 20 ; failed = 2 + 3 = 5
    assert result == {"total": 20, "failed": 5}
    assert queue_client.get.await_count == 4


@pytest.mark.asyncio
async def test_make_error_rate_fn_treats_missing_counters_as_zero() -> None:
    """None counters from Valkey are coerced to zero."""
    queue_client = MagicMock()
    queue_client.get = AsyncMock(return_value=None)

    fn = HealthService._make_error_rate_fn(queue_client)
    result = await fn()

    assert result == {"total": 0, "failed": 0}


def test_make_database_adapter_fn_returns_connected_adapter(
    ollama_settings: MagicMock,
) -> None:
    """The database adapter fn resolves an adapter for the current database."""
    service = HealthService(settings=ollama_settings, queue_client=None)
    fn = service._make_database_adapter_fn()

    fake_adapter = MagicMock()
    with patch(
        "chaoscypher_core.database.get_sqlite_adapter", return_value=fake_adapter
    ) as mock_get:
        adapter = fn()

    assert adapter is fake_adapter
    mock_get.assert_called_once_with(database_name="default")


def test_queue_client_registers_error_rate_probe(ollama_settings: MagicMock) -> None:
    """Supplying a queue_client registers the ErrorRateProbe + live worker fns."""
    queue_client = MagicMock()
    queue_client.ping = AsyncMock()
    service = HealthService(settings=ollama_settings, queue_client=queue_client)

    names = set(service.registry.probes.keys())
    # Worker probes + error rate probe present.
    assert "llm_worker" in names
    assert "ops_worker" in names
    assert "queue" in names
    assert "error_rate" in names
