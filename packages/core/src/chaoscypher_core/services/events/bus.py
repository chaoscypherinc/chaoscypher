# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Centralized event bus for system-level event recording.

Configure once at startup with an adapter, then emit events from
anywhere. If no adapter is configured (CLI, tests), events are
silently dropped.

Usage:
    from chaoscypher_core.services.events import event_bus

    # At startup:
    event_bus.configure(adapter)

    # Anywhere:
    event_bus.emit("file_uploaded", action="File uploaded: paper.pdf", details={...})
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

logger = structlog.get_logger(__name__)


class EventBus:
    """Singleton event bus for recording system events."""

    def __init__(self) -> None:
        """Initialize the event bus with no adapter."""
        self._adapter: SqliteAdapter | None = None

    def configure(self, adapter: SqliteAdapter) -> None:
        """Set the storage adapter for event persistence.

        Args:
            adapter: Storage adapter exposing ``record_system_event``.
        """
        self._adapter = adapter
        logger.debug("event_bus_configured")

    def emit(
        self,
        event_type: str,
        *,
        action: str,
        source: str | None = None,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
        database_name: str | None = None,
    ) -> None:
        """Record a system event. Silent no-op if unconfigured.

        Args:
            event_type: Event category (e.g. ``"file_uploaded"``).
            action: Human-readable description of the event.
            source: Who triggered the event (e.g. ``"worker"``).
            reason: Optional human-readable reason string.
            details: Optional dict serialised to JSON for extra context.
            database_name: Scoped database name, if applicable.
        """
        if self._adapter is None:
            return
        try:
            self._adapter.record_system_event(
                event_type=event_type,
                action=action,
                source=source,
                reason=reason,
                details=json.dumps(details) if details else None,
                database_name=database_name,
            )
        except Exception:
            logger.debug(
                "event_bus_emit_failed",
                event_type=event_type,
                action=action,
            )

    @property
    def is_configured(self) -> bool:
        """Check if the bus has an adapter."""
        return self._adapter is not None


# Module-level singleton
event_bus = EventBus()
