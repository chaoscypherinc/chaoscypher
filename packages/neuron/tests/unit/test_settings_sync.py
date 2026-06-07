# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for settings change listener and hot-reload logic.

Covers reload_llm_provider with mock context, connection error handling,
and the asyncio lock preventing concurrent reloads.
"""

import asyncio
import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_neuron.settings_sync import (
    _apply_logging_level,
    reload_llm_provider,
)


# ============================================================================
# Helpers
# ============================================================================


def _make_worker_context(**overrides):
    """Build a minimal WorkerContext dict for testing reload_llm_provider."""
    mock_settings = MagicMock()
    mock_settings.llm.chat_provider = "ollama"
    mock_settings.llm.ollama_chat_model = "llama3"
    mock_settings.llm.ollama_extraction_model = "llama3"
    mock_settings.llm.ollama_instances = []
    mock_settings.embedding.model = "all-MiniLM-L6-v2"
    mock_settings.search.vector_dimensions = 384
    mock_settings.current_database = "test_db"

    mock_config_manager = MagicMock()
    mock_config_manager.get_settings.return_value = mock_settings
    mock_config_manager.invalidate_cache = MagicMock()

    mock_search_repo = MagicMock()
    mock_search_repo.vector_dim = 384

    ctx = {
        "config_manager": mock_config_manager,
        "settings": mock_settings,
        "llm_provider": MagicMock(),
        "llm_service": MagicMock(),
        "engine_settings": MagicMock(),
        "graph_repository": MagicMock(),
        "search_repository": mock_search_repo,
        "current_database": "test_db",
        "storage_adapter": MagicMock(),
    }
    ctx.update(overrides)
    return ctx


@contextlib.contextmanager
def _patch_reload_deps():
    """Patch all function-local imports used by reload_llm_provider at their source."""
    with (
        patch("chaoscypher_core.app_config.engine_factory.build_engine_settings") as mock_build,
        patch("chaoscypher_core.llm_queue.LLMProvider") as mock_llm_cls,
        patch("chaoscypher_core.llm_queue.queue_service.LLMQueueService") as mock_svc_cls,
        patch(
            "chaoscypher_neuron.setup.llm_handlers.setup_llm_handlers",
            new_callable=AsyncMock,
        ) as mock_llm_setup,
        patch(
            "chaoscypher_neuron.setup.ops_handlers.setup_operations_handlers",
            new_callable=AsyncMock,
        ) as mock_ops_setup,
        patch("chaoscypher_core.app_config.get_settings") as mock_get_settings,
        patch("chaoscypher_core.app_config.set_settings"),
    ):
        mock_get_settings.cache_clear = MagicMock()
        yield {
            "build": mock_build,
            "llm_cls": mock_llm_cls,
            "svc_cls": mock_svc_cls,
            "llm_setup": mock_llm_setup,
            "ops_setup": mock_ops_setup,
        }


# ============================================================================
# reload_llm_provider
# ============================================================================


class TestReloadLlmProvider:
    """Tests for reload_llm_provider."""

    @pytest.mark.asyncio
    async def test_reload_creates_new_provider(self) -> None:
        """reload_llm_provider creates a new LLMProvider with fresh settings."""
        ctx = _make_worker_context()

        with _patch_reload_deps() as patches:
            mock_new_provider = MagicMock()
            patches["llm_cls"].return_value = mock_new_provider
            mock_new_service = MagicMock()
            patches["svc_cls"].return_value = mock_new_service

            await reload_llm_provider(ctx)

        # Config manager had its cache invalidated
        ctx["config_manager"].invalidate_cache.assert_called_once()

        # New provider was created and placed in context
        assert ctx["llm_provider"] is mock_new_provider
        assert ctx["llm_service"] is mock_new_service

    @pytest.mark.asyncio
    async def test_reload_skips_when_no_config_manager(self) -> None:
        """reload_llm_provider returns early if config_manager is missing."""
        ctx = _make_worker_context()
        ctx["config_manager"] = None  # Explicitly None

        original_settings = ctx["settings"]
        await reload_llm_provider(ctx)

        # Settings should be unchanged (nothing happened)
        assert ctx["settings"] is original_settings

    @pytest.mark.asyncio
    async def test_reload_restores_context_on_failure(self) -> None:
        """On failure, reload_llm_provider restores previous context values."""
        ctx = _make_worker_context()
        original_settings = ctx["settings"]
        original_provider = ctx["llm_provider"]
        original_service = ctx["llm_service"]
        original_engine_settings = ctx["engine_settings"]

        with (
            patch(
                "chaoscypher_core.app_config.engine_factory.build_engine_settings",
                side_effect=RuntimeError("conversion failed"),
            ),
            patch("chaoscypher_core.app_config.get_settings") as mock_gs,
            patch("chaoscypher_core.app_config.set_settings"),
        ):
            mock_gs.cache_clear = MagicMock()
            await reload_llm_provider(ctx)

        # Previous context values are restored
        assert ctx["settings"] is original_settings
        assert ctx["llm_provider"] is original_provider
        assert ctx["llm_service"] is original_service
        assert ctx["engine_settings"] is original_engine_settings

    @pytest.mark.asyncio
    async def test_reload_re_registers_handlers(self) -> None:
        """reload_llm_provider re-registers both LLM and operations handlers."""
        ctx = _make_worker_context()

        with _patch_reload_deps() as patches:
            await reload_llm_provider(ctx)

        patches["llm_setup"].assert_called_once_with(ctx)
        patches["ops_setup"].assert_called_once_with(ctx)

    @pytest.mark.asyncio
    async def test_reload_recreates_search_repo_on_dimension_change(self) -> None:
        """When vector dimensions change, search repository is recreated."""
        ctx = _make_worker_context()
        ctx["search_repository"].vector_dim = 384  # Old dimensions

        # New settings have different dimensions
        new_settings = MagicMock()
        new_settings.search.vector_dimensions = 768  # Changed!
        new_settings.embedding.model = "new-model"
        new_settings.llm.chat_provider = "ollama"
        new_settings.llm.ollama_chat_model = "llama3"
        new_settings.llm.ollama_extraction_model = "llama3"
        new_settings.llm.ollama_instances = []
        new_settings.current_database = "test_db"

        ctx["config_manager"].get_settings.return_value = new_settings

        mock_new_search_repo = MagicMock()

        with (
            _patch_reload_deps(),
            patch(
                "chaoscypher_core.adapters.sqlite.repos.SearchRepository",
                return_value=mock_new_search_repo,
            ),
            patch("chaoscypher_core.database.engine.get_engine"),
        ):
            await reload_llm_provider(ctx)

        assert ctx["search_repository"] is mock_new_search_repo


# ============================================================================
# Lock Prevents Concurrent Reloads
# ============================================================================


class TestReloadLock:
    """Tests that the asyncio lock prevents concurrent reloads."""

    @pytest.mark.asyncio
    async def test_lock_serializes_concurrent_reloads(self) -> None:
        """Two concurrent reload calls are serialized by the lock."""
        call_order = []

        async def slow_llm_setup(*args, **kwargs):
            call_order.append("start")
            await asyncio.sleep(0.05)
            call_order.append("end")

        ctx = _make_worker_context()

        with (
            patch("chaoscypher_core.app_config.engine_factory.build_engine_settings"),
            patch("chaoscypher_core.llm_queue.LLMProvider"),
            patch("chaoscypher_core.llm_queue.queue_service.LLMQueueService"),
            patch(
                "chaoscypher_neuron.setup.llm_handlers.setup_llm_handlers",
                side_effect=slow_llm_setup,
            ),
            patch(
                "chaoscypher_neuron.setup.ops_handlers.setup_operations_handlers",
                new_callable=AsyncMock,
            ),
            patch("chaoscypher_core.app_config.get_settings") as mock_gs,
            patch("chaoscypher_core.app_config.set_settings"),
        ):
            mock_gs.cache_clear = MagicMock()
            await asyncio.gather(
                reload_llm_provider(ctx),
                reload_llm_provider(ctx),
            )

        # Both reloads completed
        assert call_order.count("start") == 2
        assert call_order.count("end") == 2
        # Serialized: first end must come before second start
        assert call_order == ["start", "end", "start", "end"]


# ============================================================================
# Settings Listener
# ============================================================================


class TestListenForSettingsChanges:
    """Tests for listen_for_settings_changes."""

    @pytest.mark.asyncio
    async def test_listener_exits_when_no_queue_client(self) -> None:
        """listen_for_settings_changes returns immediately if queue client is None."""
        from chaoscypher_neuron.settings_sync import listen_for_settings_changes

        ctx = _make_worker_context()

        with patch("chaoscypher_neuron.settings_sync.queue_client") as mock_qc:
            mock_qc.client = None
            await listen_for_settings_changes(ctx)

    @pytest.mark.asyncio
    async def test_listener_exits_when_no_settings(self) -> None:
        """listen_for_settings_changes returns if settings are not in context."""
        from chaoscypher_neuron.settings_sync import listen_for_settings_changes

        ctx = _make_worker_context()
        ctx["settings"] = None  # type: ignore[assignment]

        with patch("chaoscypher_neuron.settings_sync.queue_client") as mock_qc:
            mock_qc.client = MagicMock()
            await listen_for_settings_changes(ctx)


# ============================================================================
# Logging Level Application
# ============================================================================


class TestApplyLoggingLevel:
    """Tests for _apply_logging_level."""

    def test_applies_debug_level(self) -> None:
        """_apply_logging_level sets root logger to DEBUG."""
        root = logging.getLogger()
        old_level = root.level

        try:
            _apply_logging_level("DEBUG")
            assert root.level == logging.DEBUG
        finally:
            root.setLevel(old_level)

    def test_applies_warning_level(self) -> None:
        """_apply_logging_level sets root logger to WARNING."""
        root = logging.getLogger()
        old_level = root.level

        try:
            _apply_logging_level("WARNING")
            assert root.level == logging.WARNING
        finally:
            root.setLevel(old_level)

    def test_case_insensitive(self) -> None:
        """_apply_logging_level handles lowercase input."""
        root = logging.getLogger()
        old_level = root.level

        try:
            _apply_logging_level("error")
            assert root.level == logging.ERROR
        finally:
            root.setLevel(old_level)

    def test_ignores_invalid_level(self) -> None:
        """_apply_logging_level ignores invalid level strings."""
        root = logging.getLogger()
        old_level = root.level

        _apply_logging_level("NOT_A_VALID_LEVEL")

        assert root.level == old_level


# ============================================================================
# Message Versioning
# ============================================================================


class TestMessageVersioning:
    """Tests for pub/sub message versioning (v1: prefix gate)."""

    @pytest.mark.asyncio
    async def test_subscriber_parses_v1_logging_level(self) -> None:
        """Subscriber correctly handles a v1:logging_level:<level> message."""
        from chaoscypher_neuron.settings_sync import listen_for_settings_changes

        ctx = _make_worker_context()

        # Two messages: a v1 logging_level, then cancel
        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": b"v1:logging_level:DEBUG"},
        ]

        async def _gen():
            for m in messages:
                yield m

        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.listen.return_value = _gen()

        mock_valkey_instance = MagicMock()
        mock_valkey_instance.pubsub.return_value = mock_pubsub
        mock_valkey_instance.aclose = AsyncMock()

        root = logging.getLogger()
        old_level = root.level
        root.setLevel(logging.WARNING)

        try:
            with (
                patch("chaoscypher_neuron.settings_sync.queue_client") as mock_qc,
                patch(
                    "chaoscypher_neuron.settings_sync._create_pubsub_client",
                    return_value=mock_valkey_instance,
                ),
                patch(
                    "chaoscypher_neuron.settings_sync.reload_llm_provider",
                    new_callable=AsyncMock,
                ) as mock_reload,
            ):
                mock_qc.client = MagicMock()

                # The listener loops forever; we cancel it after one iteration
                task = asyncio.create_task(listen_for_settings_changes(ctx))
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            # Level was changed to DEBUG — subscriber parsed v1: payload correctly
            assert root.level == logging.DEBUG
            # reload_llm_provider was NOT called for a logging_level message
            mock_reload.assert_not_called()
        finally:
            root.setLevel(old_level)

    @pytest.mark.asyncio
    async def test_subscriber_parses_v1_llm_settings_updated(self) -> None:
        """Subscriber calls reload_llm_provider for a v1:llm_settings_updated message."""
        from chaoscypher_neuron.settings_sync import listen_for_settings_changes

        ctx = _make_worker_context()

        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": b"v1:llm_settings_updated"},
        ]

        async def _gen():
            for m in messages:
                yield m

        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.listen.return_value = _gen()

        mock_valkey_instance = MagicMock()
        mock_valkey_instance.pubsub.return_value = mock_pubsub
        mock_valkey_instance.aclose = AsyncMock()

        with (
            patch("chaoscypher_neuron.settings_sync.queue_client") as mock_qc,
            patch(
                "chaoscypher_neuron.settings_sync._create_pubsub_client",
                return_value=mock_valkey_instance,
            ),
            patch(
                "chaoscypher_neuron.settings_sync.reload_llm_provider",
                new_callable=AsyncMock,
            ) as mock_reload,
        ):
            mock_qc.client = MagicMock()

            task = asyncio.create_task(listen_for_settings_changes(ctx))
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        mock_reload.assert_called_once_with(ctx)

    @pytest.mark.asyncio
    async def test_subscriber_skips_unknown_version(self) -> None:
        """Subscriber logs a warning and skips messages without a v1: prefix."""
        from chaoscypher_neuron.settings_sync import listen_for_settings_changes

        ctx = _make_worker_context()

        messages = [
            {"type": "subscribe", "data": 1},
            # Bare (unversioned) message — the format before v1 was added
            {"type": "message", "data": b"logging_level:DEBUG"},
            # Future version — must also be skipped
            {"type": "message", "data": b"v2:something_new"},
        ]

        async def _gen():
            for m in messages:
                yield m

        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.listen.return_value = _gen()

        mock_valkey_instance = MagicMock()
        mock_valkey_instance.pubsub.return_value = mock_pubsub
        mock_valkey_instance.aclose = AsyncMock()

        root = logging.getLogger()
        old_level = root.level
        root.setLevel(logging.WARNING)

        try:
            with (
                patch("chaoscypher_neuron.settings_sync.queue_client") as mock_qc,
                patch(
                    "chaoscypher_neuron.settings_sync._create_pubsub_client",
                    return_value=mock_valkey_instance,
                ),
                patch(
                    "chaoscypher_neuron.settings_sync.reload_llm_provider",
                    new_callable=AsyncMock,
                ) as mock_reload,
            ):
                mock_qc.client = MagicMock()

                task = asyncio.create_task(listen_for_settings_changes(ctx))
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            # Level must NOT have changed — bare message was skipped
            assert root.level == logging.WARNING
            # reload_llm_provider must NOT have been called — v2 was skipped
            mock_reload.assert_not_called()
        finally:
            root.setLevel(old_level)


# ============================================================================
# Version Counter Reconciliation (Task 7.4)
# ============================================================================


class TestVersionReconciliation:
    """Tests for durable version counter reconciliation on connect."""

    @pytest.mark.asyncio
    async def test_subscriber_catches_up_when_known_version_behind(self) -> None:
        """On connect, if live version > known version, subscriber reloads settings."""
        from chaoscypher_neuron.settings_sync import listen_for_settings_changes

        ctx = _make_worker_context()

        # Only a subscribe confirmation — no real messages, task will be cancelled.
        messages = [{"type": "subscribe", "data": 1}]

        async def _gen():
            for m in messages:
                yield m

        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.listen.return_value = _gen()

        # Valkey GET returns version=5; our known version is 3 (behind).
        mock_valkey_instance = MagicMock()
        mock_valkey_instance.pubsub.return_value = mock_pubsub
        mock_valkey_instance.aclose = AsyncMock()
        mock_valkey_instance.get = AsyncMock(return_value=b"5")

        with (
            patch("chaoscypher_neuron.settings_sync.queue_client") as mock_qc,
            patch(
                "chaoscypher_neuron.settings_sync._create_pubsub_client",
                return_value=mock_valkey_instance,
            ),
            patch(
                "chaoscypher_neuron.settings_sync.reload_llm_provider",
                new_callable=AsyncMock,
            ) as mock_reload,
        ):
            mock_qc.client = MagicMock()

            # Inject a known_version of 3 so we're behind live=5.
            with patch("chaoscypher_neuron.settings_sync._known_version", 3):
                task = asyncio.create_task(listen_for_settings_changes(ctx))
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # Should have triggered a reload because live (5) > known (3).
        mock_reload.assert_called_once_with(ctx)

    @pytest.mark.asyncio
    async def test_subscriber_no_catchup_when_at_latest(self) -> None:
        """If known_version == live version, no reload on connect."""
        from chaoscypher_neuron.settings_sync import listen_for_settings_changes

        ctx = _make_worker_context()

        messages = [{"type": "subscribe", "data": 1}]

        async def _gen():
            for m in messages:
                yield m

        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.listen.return_value = _gen()

        # Live version=5, known_version=5 — already current.
        mock_valkey_instance = MagicMock()
        mock_valkey_instance.pubsub.return_value = mock_pubsub
        mock_valkey_instance.aclose = AsyncMock()
        mock_valkey_instance.get = AsyncMock(return_value=b"5")

        with (
            patch("chaoscypher_neuron.settings_sync.queue_client") as mock_qc,
            patch(
                "chaoscypher_neuron.settings_sync._create_pubsub_client",
                return_value=mock_valkey_instance,
            ),
            patch(
                "chaoscypher_neuron.settings_sync.reload_llm_provider",
                new_callable=AsyncMock,
            ) as mock_reload,
        ):
            mock_qc.client = MagicMock()

            with patch("chaoscypher_neuron.settings_sync._known_version", 5):
                task = asyncio.create_task(listen_for_settings_changes(ctx))
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        mock_reload.assert_not_called()

    @pytest.mark.asyncio
    async def test_subscriber_no_catchup_when_version_missing(self) -> None:
        """First-time connect with no version key (GET returns None) treats live=0; no reload."""
        from chaoscypher_neuron.settings_sync import listen_for_settings_changes

        ctx = _make_worker_context()

        messages = [{"type": "subscribe", "data": 1}]

        async def _gen():
            for m in messages:
                yield m

        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.listen.return_value = _gen()

        # Key doesn't exist yet — GET returns None.
        mock_valkey_instance = MagicMock()
        mock_valkey_instance.pubsub.return_value = mock_pubsub
        mock_valkey_instance.aclose = AsyncMock()
        mock_valkey_instance.get = AsyncMock(return_value=None)

        with (
            patch("chaoscypher_neuron.settings_sync.queue_client") as mock_qc,
            patch(
                "chaoscypher_neuron.settings_sync._create_pubsub_client",
                return_value=mock_valkey_instance,
            ),
            patch(
                "chaoscypher_neuron.settings_sync.reload_llm_provider",
                new_callable=AsyncMock,
            ) as mock_reload,
        ):
            mock_qc.client = MagicMock()

            # known_version=0, live=0 (missing key) — no catchup needed.
            with patch("chaoscypher_neuron.settings_sync._known_version", 0):
                task = asyncio.create_task(listen_for_settings_changes(ctx))
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        mock_reload.assert_not_called()
