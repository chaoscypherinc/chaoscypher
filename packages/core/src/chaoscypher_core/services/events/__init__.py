# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Events -- centralized event bus and health monitoring.

The event bus is a singleton that any code can use to record
system-level events. Health monitoring is a sub-module that
produces health check events and auto-pause decisions.

Usage:
    from chaoscypher_core.services.events import event_bus
    event_bus.emit("file_uploaded", action="File uploaded", details={...})

    from chaoscypher_core.services.events.health import HealthRegistry
"""

from chaoscypher_core.services.events.bus import EventBus, event_bus


__all__ = [
    "EventBus",
    "event_bus",
]
