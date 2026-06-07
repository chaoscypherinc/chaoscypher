# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage tests for SettingsService internals.

Targets the large untested blocks in ``service.py``:

- ``verify_ollama_url`` — every ``error_type`` branch (success, not-ollama,
  ConnectError, Timeout, InvalidURL, generic, blocked-URL).
- ``update_settings`` — auto-embedding trigger sync (success + swallow path)
  and the LLM-reload branch.
- ``get_update_warnings`` — vector-dimension change branches.
- ``_maybe_reload_load_balancer`` and ``_reload_llm_services``.
- ``reset_to_defaults`` and a couple of one-line logging delegators.

``notify_workers_llm_settings_changed`` is intentionally NOT retested here
(already covered by ``test_service.py``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from chaoscypher_cortex.features.settings.service import SettingsService


# ---------------------------------------------------------------------------
# Local helpers (copied locally — cannot import across test modules under
# --import-mode=importlib).
# ---------------------------------------------------------------------------


def _make_service(
    *,
    settings_manager: MagicMock | None = None,
    logging_service: MagicMock | None = None,
    database_name: str = "default",
) -> SettingsService:
    """Build a SettingsService with mock dependencies."""
    return SettingsService(
        settings_manager=settings_manager or MagicMock(),
        database_name=database_name,
        logging_service=logging_service or MagicMock(),
    )


def _make_async_client(get_side_effect=None, get_return=None) -> MagicMock:
    """Build a MagicMock that mimics ``httpx.AsyncClient`` as an async CM.

    ``httpx.AsyncClient(timeout=...)`` is called, then used as
    ``async with ... as client`` and ``await client.get(url)`` is invoked.
    """
    client = MagicMock()
    client.get = AsyncMock(side_effect=get_side_effect, return_value=get_return)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=cm)
    return factory, client


def _resp(text: str = "", status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    """Build a fake httpx Response."""
    r = MagicMock()
    r.text = text
    r.status_code = status_code
    r.json = MagicMock(return_value=json_data or {})
    return r


# ===========================================================================
# verify_ollama_url
# ===========================================================================


class TestVerifyOllamaUrl:
    """Exercise every error_type branch of verify_ollama_url."""

    @pytest.mark.asyncio
    async def test_success_with_models_and_version(self) -> None:
        service = _make_service()

        root = _resp(text="Ollama is running")
        tags = _resp(json_data={"models": [{"name": "llama3"}, {"model": "qwen3"}]})
        version = _resp(json_data={"version": "0.5.1"})

        factory, client = _make_async_client()
        client.get.side_effect = [root, tags, version]

        with patch("httpx.AsyncClient", factory):
            result = await service.verify_ollama_url("http://localhost:11434/", 5)

        assert result["success"] is True
        assert result["message"] == "Ollama is running"
        assert result["version"] == "0.5.1"
        assert set(result["models"]) == {"llama3", "qwen3"}
        assert result["model_count"] == 2
        assert "response_time_ms" in result

    @pytest.mark.asyncio
    async def test_success_when_tags_and_version_endpoints_fail(self) -> None:
        """Models/version sub-requests may fail without failing the verify."""
        service = _make_service()

        root = _resp(text="Ollama is running")

        factory, client = _make_async_client()
        # root ok, then /api/tags raises, then /api/version raises
        client.get.side_effect = [root, RuntimeError("tags down"), RuntimeError("ver down")]

        with patch("httpx.AsyncClient", factory):
            result = await service.verify_ollama_url("http://localhost:11434", 5)

        assert result["success"] is True
        assert result["models"] == []
        assert result["version"] is None
        assert result["model_count"] == 0

    @pytest.mark.asyncio
    async def test_success_when_tags_non_200(self) -> None:
        """A non-200 /api/tags leaves models empty but verify still succeeds."""
        service = _make_service()

        root = _resp(text="Ollama is running")
        tags = _resp(status_code=503, json_data={"models": [{"name": "ignored"}]})
        version = _resp(status_code=404, json_data={"version": "ignored"})

        factory, client = _make_async_client()
        client.get.side_effect = [root, tags, version]

        with patch("httpx.AsyncClient", factory):
            result = await service.verify_ollama_url("http://localhost:11434", 5)

        assert result["success"] is True
        assert result["models"] == []
        assert result["version"] is None

    @pytest.mark.asyncio
    async def test_not_an_ollama_instance(self) -> None:
        service = _make_service()

        root = _resp(text="nginx welcome page")
        factory, client = _make_async_client()
        client.get.side_effect = [root]

        with patch("httpx.AsyncClient", factory):
            result = await service.verify_ollama_url("http://localhost:8080", 5)

        assert result["success"] is False
        assert result["error_type"] == "invalid_response"

    @pytest.mark.asyncio
    async def test_connection_refused(self) -> None:
        service = _make_service()

        factory, client = _make_async_client()
        client.get.side_effect = httpx.ConnectError("refused")

        with patch("httpx.AsyncClient", factory):
            result = await service.verify_ollama_url("http://localhost:11434", 5)

        assert result["success"] is False
        assert result["error_type"] == "connection_refused"

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        service = _make_service()

        factory, client = _make_async_client()
        client.get.side_effect = httpx.TimeoutException("slow")

        with patch("httpx.AsyncClient", factory):
            result = await service.verify_ollama_url("http://localhost:11434", 3)

        assert result["success"] is False
        assert result["error_type"] == "timeout"
        assert "3s" in result["message"]

    @pytest.mark.asyncio
    async def test_invalid_url(self) -> None:
        service = _make_service()

        factory, client = _make_async_client()
        client.get.side_effect = httpx.InvalidURL("bad")

        with patch("httpx.AsyncClient", factory):
            result = await service.verify_ollama_url("http://localhost:11434", 5)

        assert result["success"] is False
        assert result["error_type"] == "invalid_url"

    @pytest.mark.asyncio
    async def test_generic_unexpected_error(self) -> None:
        service = _make_service()

        factory, client = _make_async_client()
        client.get.side_effect = RuntimeError("kaboom")

        with patch("httpx.AsyncClient", factory):
            result = await service.verify_ollama_url("http://localhost:11434", 5)

        assert result["success"] is False
        assert result["error_type"] == "unexpected_error"

    @pytest.mark.asyncio
    async def test_blocked_url_short_circuits_before_request(self) -> None:
        """A URL failing url-safety validation never opens a connection."""
        service = _make_service()

        factory, _client = _make_async_client()

        with (
            patch(
                "chaoscypher_cortex.features.settings.service.validate_url_safety",
                return_value=False,
            ),
            patch("httpx.AsyncClient", factory) as patched_client,
        ):
            result = await service.verify_ollama_url("http://169.254.169.254", 5)

        assert result["success"] is False
        assert result["error_type"] == "blocked_url"
        # No HTTP client should have been constructed.
        patched_client.assert_not_called()


# ===========================================================================
# update_settings
# ===========================================================================


def _settings_with(auto_embedding: bool, *, chat_provider: str = "openai") -> MagicMock:
    """Build a fake Settings object with the nested fields update_settings reads."""
    s = MagicMock()
    s.search.enable_auto_embedding = auto_embedding
    s.llm.chat_provider = chat_provider
    s.llm.ollama_instances = []
    return s


class TestUpdateSettings:
    @pytest.mark.asyncio
    async def test_triggers_auto_embedding_sync_when_changed(self) -> None:
        old = _settings_with(False)
        new = _settings_with(True)
        manager = MagicMock()
        manager.get_settings.return_value = old
        manager.update_settings.return_value = new

        service = _make_service(settings_manager=manager)
        service.trigger_sync_service = MagicMock()

        result = await service.update_settings({"search": {"enable_auto_embedding": True}})

        assert result is new
        service.trigger_sync_service.sync_auto_embedding_triggers.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_swallows_trigger_sync_failure(self) -> None:
        old = _settings_with(False)
        new = _settings_with(True)
        manager = MagicMock()
        manager.get_settings.return_value = old
        manager.update_settings.return_value = new

        service = _make_service(settings_manager=manager)
        service.trigger_sync_service = MagicMock()
        service.trigger_sync_service.sync_auto_embedding_triggers.side_effect = RuntimeError(
            "atomic sync blew up"
        )

        # Must not raise — the failure is logged as a warning.
        result = await service.update_settings({"search": {"enable_auto_embedding": True}})
        assert result is new

    @pytest.mark.asyncio
    async def test_no_sync_when_unchanged(self) -> None:
        old = _settings_with(True)
        new = _settings_with(True)
        manager = MagicMock()
        manager.get_settings.return_value = old
        manager.update_settings.return_value = new

        service = _make_service(settings_manager=manager)
        service.trigger_sync_service = MagicMock()

        await service.update_settings({"dark_mode": True})
        service.trigger_sync_service.sync_auto_embedding_triggers.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_update_reloads_services(self) -> None:
        old = _settings_with(True, chat_provider="ollama")
        new = _settings_with(True, chat_provider="ollama")
        manager = MagicMock()
        manager.get_settings.return_value = old
        manager.update_settings.return_value = new

        service = _make_service(settings_manager=manager)
        service.trigger_sync_service = None

        with (
            patch.object(service, "_maybe_reload_load_balancer", new=AsyncMock()) as reload_lb,
            patch.object(service, "_reload_llm_services") as reload_llm,
        ):
            await service.update_settings({"llm": {"chat_provider": "ollama"}})

        reload_lb.assert_awaited_once_with(new)
        reload_llm.assert_called_once()


# ===========================================================================
# get_update_warnings
# ===========================================================================


def _dim_settings(dim: int) -> SimpleNamespace:
    return SimpleNamespace(search=SimpleNamespace(vector_dimensions=dim))


class TestGetUpdateWarnings:
    def test_no_warning_when_dimensions_unchanged(self) -> None:
        service = _make_service()
        warnings = service.get_update_warnings(_dim_settings(768), _dim_settings(768))
        assert warnings == []

    def test_warning_when_existing_embeddings(self) -> None:
        service = _make_service()

        search_repo = MagicMock()
        search_repo.vector.get_vector_count.return_value = 1234

        with patch(
            "chaoscypher_core.repo_factories.get_search_repository",
            return_value=search_repo,
        ):
            warnings = service.get_update_warnings(_dim_settings(768), _dim_settings(1024))

        assert len(warnings) == 1
        assert warnings[0].field == "search.vector_dimensions"
        assert warnings[0].severity == "warning"
        assert "1,234" in warnings[0].message

    def test_info_when_no_existing_embeddings(self) -> None:
        service = _make_service()

        search_repo = MagicMock()
        search_repo.vector.get_vector_count.return_value = 0

        with patch(
            "chaoscypher_core.repo_factories.get_search_repository",
            return_value=search_repo,
        ):
            warnings = service.get_update_warnings(_dim_settings(768), _dim_settings(1024))

        assert len(warnings) == 1
        assert warnings[0].severity == "info"
        assert "no impact" in warnings[0].message.lower()

    def test_repo_failure_treated_as_zero_vectors(self) -> None:
        """If the search repo can't be built, treat as no existing embeddings."""
        service = _make_service()

        with patch(
            "chaoscypher_core.repo_factories.get_search_repository",
            side_effect=RuntimeError("repo init failed"),
        ):
            warnings = service.get_update_warnings(_dim_settings(384), _dim_settings(768))

        assert len(warnings) == 1
        assert warnings[0].severity == "info"


# ===========================================================================
# _maybe_reload_load_balancer
# ===========================================================================


class TestMaybeReloadLoadBalancer:
    @pytest.mark.asyncio
    async def test_skips_when_not_ollama(self) -> None:
        service = _make_service()
        settings = MagicMock()
        settings.llm.chat_provider = "openai"

        with patch(
            "chaoscypher_core.adapters.llm.load_balancer.get_ollama_load_balancer"
        ) as get_lb:
            await service._maybe_reload_load_balancer(settings)
        get_lb.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_instances(self) -> None:
        service = _make_service()
        settings = MagicMock()
        settings.llm.chat_provider = "ollama"
        settings.llm.ollama_instances = []

        with patch(
            "chaoscypher_core.adapters.llm.load_balancer.get_ollama_load_balancer"
        ) as get_lb:
            await service._maybe_reload_load_balancer(settings)
        get_lb.assert_not_called()

    @pytest.mark.asyncio
    async def test_reloads_when_ollama_with_instances(self) -> None:
        service = _make_service()
        settings = MagicMock()
        settings.llm.chat_provider = "ollama"
        settings.llm.ollama_instances = [MagicMock()]
        settings.llm.ollama_load_balancing = "round_robin"

        lb = MagicMock()
        lb.reload_config = AsyncMock()

        with patch(
            "chaoscypher_core.adapters.llm.load_balancer.get_ollama_load_balancer",
            return_value=lb,
        ):
            await service._maybe_reload_load_balancer(settings)

        lb.reload_config.assert_awaited_once_with(settings.llm)

    @pytest.mark.asyncio
    async def test_swallows_reload_failure(self) -> None:
        service = _make_service()
        settings = MagicMock()
        settings.llm.chat_provider = "ollama"
        settings.llm.ollama_instances = [MagicMock()]

        with patch(
            "chaoscypher_core.adapters.llm.load_balancer.get_ollama_load_balancer",
            side_effect=RuntimeError("no load balancer"),
        ):
            # Must not raise.
            await service._maybe_reload_load_balancer(settings)


# ===========================================================================
# _reload_llm_services
# ===========================================================================


class TestReloadLlmServices:
    def test_reloads_queue_service(self) -> None:
        service = _make_service()
        with patch("chaoscypher_core.llm_queue.queue_factory.reload_llm_queue_service") as reload:
            service._reload_llm_services()
        reload.assert_called_once()

    def test_swallows_reload_failure(self) -> None:
        service = _make_service()
        with patch(
            "chaoscypher_core.llm_queue.queue_factory.reload_llm_queue_service",
            side_effect=RuntimeError("queue down"),
        ):
            # Must not raise.
            service._reload_llm_services()


# ===========================================================================
# reset_to_defaults + logging delegators
# ===========================================================================


def test_reset_to_defaults_delegates_to_manager() -> None:
    sentinel = object()
    manager = MagicMock()
    manager.reset_to_defaults.return_value = sentinel

    service = _make_service(settings_manager=manager)
    assert service.reset_to_defaults() is sentinel
    manager.reset_to_defaults.assert_called_once_with()


def test_get_logging_level_delegates() -> None:
    logging_service = MagicMock()
    sentinel = object()
    logging_service.get_logging_level.return_value = sentinel

    service = _make_service(logging_service=logging_service)
    assert service.get_logging_level() is sentinel


def test_set_logging_level_delegates() -> None:
    logging_service = MagicMock()
    sentinel = object()
    logging_service.set_logging_level.return_value = sentinel

    service = _make_service(logging_service=logging_service)
    assert service.set_logging_level("DEBUG") is sentinel
    logging_service.set_logging_level.assert_called_once_with("DEBUG")
