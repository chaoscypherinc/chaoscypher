# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Logging Service.

Handles runtime logging level management with cross-process hot-reload.
Separated from SettingsService to follow Single Responsibility Principle.

Responsibilities:
- Get current logging level
- Set logging level dynamically (cortex process)
- Notify neuron worker via Valkey pub/sub to sync log level
"""

import logging

import structlog

from chaoscypher_cortex.features.settings.models import (
    LoggingLevelResponse,
    SetLoggingLevelResponse,
)


logger = structlog.get_logger(__name__)


class LoggingService:
    """Service for runtime logging level management.

    Allows dynamic control of application logging without restart.
    Changes propagate to both cortex and neuron via Valkey pub/sub.
    """

    def get_logging_level(self) -> LoggingLevelResponse:
        """Get current logging level for the application.

        Returns:
            LoggingLevelResponse with current level and available options

        """
        root_logger = logging.getLogger()
        current_level = logging.getLevelName(root_logger.level)

        return LoggingLevelResponse(
            level=current_level,
            numeric_level=root_logger.level,
            available_levels=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        )

    def set_logging_level(self, level: str) -> SetLoggingLevelResponse:
        """Set logging level for the application in real-time.

        Changes take effect immediately in the cortex process. The neuron
        worker is notified via Valkey pub/sub to sync its level.

        Args:
            level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL)

        Returns:
            SetLoggingLevelResponse with old and new levels

        Raises:
            AttributeError: If level string is invalid

        """
        level_str = level.upper()

        # Convert string to numeric level (raises AttributeError if invalid)
        numeric_level = getattr(logging, level_str)

        # Set level for root logger (affects all loggers in this process)
        root_logger = logging.getLogger()
        old_level = logging.getLevelName(root_logger.level)
        root_logger.setLevel(numeric_level)

        logger.info("logging_level_changed", old_level=old_level, new_level=level_str)

        return SetLoggingLevelResponse(
            success=True,
            old_level=old_level,
            new_level=level_str,
            message=f"Logging level set to {level_str}. Change is immediate, no restart required.",
        )

    async def notify_workers_logging_level(self, level: str) -> None:
        """Publish log level change to Valkey so neuron worker syncs.

        Args:
            level: The new log level string (e.g. "DEBUG", "WARNING").

        """
        from chaoscypher_cortex.shared.worker_notify import publish_settings_change

        await publish_settings_change(f"v1:logging_level:{level}")
