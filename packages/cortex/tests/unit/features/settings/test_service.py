# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SettingsService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.settings.service import SettingsService


def test_settings_service_accepts_injected_logging_service() -> None:
    """SettingsService should accept LoggingService via constructor injection.

    This test verifies that the LoggingService dependency can be provided from
    the outside (typically by the factory) rather than being constructed inside
    ``SettingsService.__init__``. Injecting the dependency makes the service
    unit-testable without monkey-patching the real LoggingService.
    """
    mock_logging = MagicMock()
    settings_manager = MagicMock()
    database_name = "test_db"

    service = SettingsService(
        settings_manager=settings_manager,
        database_name=database_name,
        logging_service=mock_logging,
    )

    assert service.logging_service is mock_logging


# ============================================================================
# notify_workers_llm_settings_changed
# ============================================================================


class TestNotifyWorkersLlmSettingsChanged:
    """Tests for SettingsService.notify_workers_llm_settings_changed — the pub/sub publisher."""

    @pytest.mark.asyncio
    async def test_publishes_v1_prefixed_message(self) -> None:
        """notify_workers_llm_settings_changed must publish 'v1:llm_settings_updated'."""
        service = SettingsService(
            settings_manager=MagicMock(),
            database_name="test_db",
            logging_service=MagicMock(),
        )

        mock_client = AsyncMock()
        mock_client.publish = AsyncMock()
        mock_client.aclose = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.queue.queue_password = ""
        mock_settings.queue.queue_host = "localhost"
        mock_settings.queue.queue_port = 6379
        mock_settings.queue.queue_database = 0

        # get_settings and valkey.asyncio.from_url are lazy-imported inside the method.
        with (
            patch(
                "chaoscypher_core.app_config.get_settings",
                return_value=mock_settings,
            ),
            patch(
                "valkey.asyncio.from_url",
                return_value=mock_client,
            ),
        ):
            await service.notify_workers_llm_settings_changed()

        mock_client.publish.assert_called_once_with(
            "chaoscypher:settings:changed",
            "v1:llm_settings_updated",
        )

    @pytest.mark.asyncio
    async def test_swallows_publish_exception(self) -> None:
        """notify_workers_llm_settings_changed does not raise if Valkey is unavailable."""
        service = SettingsService(
            settings_manager=MagicMock(),
            database_name="test_db",
            logging_service=MagicMock(),
        )

        with patch(
            "chaoscypher_core.app_config.get_settings",
            side_effect=RuntimeError("no settings"),
        ):
            # Should not raise
            await service.notify_workers_llm_settings_changed()

    @pytest.mark.asyncio
    async def test_publisher_increments_settings_version(self) -> None:
        """Each publish bumps chaoscypher:settings:version atomically via INCR."""
        service = SettingsService(
            settings_manager=MagicMock(),
            database_name="test_db",
            logging_service=MagicMock(),
        )

        mock_client = AsyncMock()
        mock_client.incr = AsyncMock(return_value=3)
        mock_client.publish = AsyncMock()
        mock_client.aclose = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.queue.queue_password = ""
        mock_settings.queue.queue_host = "localhost"
        mock_settings.queue.queue_port = 6379
        mock_settings.queue.queue_database = 0

        with (
            patch(
                "chaoscypher_core.app_config.get_settings",
                return_value=mock_settings,
            ),
            patch(
                "valkey.asyncio.from_url",
                return_value=mock_client,
            ),
        ):
            await service.notify_workers_llm_settings_changed()

        # INCR must be called on the version counter key before publish.
        mock_client.incr.assert_called_once_with("chaoscypher:settings:version")
