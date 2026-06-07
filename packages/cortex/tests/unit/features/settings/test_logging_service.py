# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for LoggingService, including the pub/sub publisher.

Covers get_logging_level, set_logging_level, and notify_workers_logging_level.
The publisher tests verify that messages are versioned with the v1: prefix.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from chaoscypher_cortex.features.settings.logging_service import LoggingService


# ============================================================================
# notify_workers_logging_level
# ============================================================================


class TestNotifyWorkersLoggingLevel:
    """Tests for notify_workers_logging_level — the pub/sub publisher."""

    @pytest.mark.asyncio
    async def test_publishes_v1_prefixed_message(self) -> None:
        """notify_workers_logging_level must publish 'v1:logging_level:<level>'."""
        service = LoggingService()

        mock_client = AsyncMock()
        mock_client.publish = AsyncMock()
        mock_client.aclose = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.queue.queue_password = ""
        mock_settings.queue.queue_host = "localhost"
        mock_settings.queue.queue_port = 6379
        mock_settings.queue.queue_database = 0

        # Both get_settings and valkey.asyncio.from_url are lazy-imported inside
        # the method, so patch at their canonical module paths.
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
            await service.notify_workers_logging_level("DEBUG")

        mock_client.publish.assert_called_once_with(
            "chaoscypher:settings:changed",
            "v1:logging_level:DEBUG",
        )

    @pytest.mark.asyncio
    async def test_publishes_v1_prefix_for_all_levels(self) -> None:
        """v1: prefix is present regardless of the log level value."""
        service = LoggingService()

        mock_client = AsyncMock()
        mock_client.publish = AsyncMock()
        mock_client.aclose = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.queue.queue_password = SecretStr("secret")
        mock_settings.queue.queue_host = "valkey"
        mock_settings.queue.queue_port = 6379
        mock_settings.queue.queue_database = 0

        for level in ("INFO", "WARNING", "ERROR", "CRITICAL"):
            mock_client.publish.reset_mock()
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
                await service.notify_workers_logging_level(level)

            published_body = mock_client.publish.call_args[0][1]
            assert published_body.startswith("v1:"), (
                f"Expected v1: prefix for level {level!r}, got {published_body!r}"
            )
            assert published_body == f"v1:logging_level:{level}"

    @pytest.mark.asyncio
    async def test_swallows_publish_exception(self) -> None:
        """notify_workers_logging_level does not raise if Valkey is unavailable."""
        service = LoggingService()

        with patch(
            "chaoscypher_core.app_config.get_settings",
            side_effect=RuntimeError("no settings"),
        ):
            # Should not raise
            await service.notify_workers_logging_level("DEBUG")

    @pytest.mark.asyncio
    async def test_publisher_increments_settings_version(self) -> None:
        """Each publish bumps chaoscypher:settings:version atomically via INCR."""
        service = LoggingService()

        mock_client = AsyncMock()
        mock_client.incr = AsyncMock(return_value=1)
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
            await service.notify_workers_logging_level("INFO")

        # INCR must be called on the version counter key before publish.
        mock_client.incr.assert_called_once_with("chaoscypher:settings:version")


# ============================================================================
# get_logging_level
# ============================================================================


class TestGetLoggingLevel:
    """Tests for get_logging_level."""

    def test_returns_current_root_level(self) -> None:
        """get_logging_level returns the root logger's current level."""
        root = logging.getLogger()
        old_level = root.level
        root.setLevel(logging.WARNING)

        try:
            service = LoggingService()
            response = service.get_logging_level()
            assert response.level == "WARNING"
            assert response.numeric_level == logging.WARNING
        finally:
            root.setLevel(old_level)

    def test_returns_available_levels(self) -> None:
        """get_logging_level includes all standard log levels."""
        service = LoggingService()
        response = service.get_logging_level()
        assert set(response.available_levels) == {
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }


# ============================================================================
# set_logging_level
# ============================================================================


class TestSetLoggingLevel:
    """Tests for set_logging_level."""

    def test_sets_root_logger_level(self) -> None:
        """set_logging_level changes the root logger level immediately."""
        root = logging.getLogger()
        old_level = root.level

        try:
            service = LoggingService()
            service.set_logging_level("DEBUG")
            assert root.level == logging.DEBUG
        finally:
            root.setLevel(old_level)

    def test_returns_old_and_new_level(self) -> None:
        """set_logging_level response contains both old and new levels."""
        root = logging.getLogger()
        old_level = root.level
        root.setLevel(logging.INFO)

        try:
            service = LoggingService()
            response = service.set_logging_level("ERROR")
            assert response.old_level == "INFO"
            assert response.new_level == "ERROR"
            assert response.success is True
        finally:
            root.setLevel(old_level)
