# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Triggers Feature.

Event-based workflow automation with scheduled and reactive triggers.

This feature provides automated workflow execution based on events, schedules,
or document commits. Supports cron-based scheduling, document import triggers,
and custom event patterns. Triggers can execute workflows automatically when
conditions are met, enabling hands-free knowledge graph maintenance and analysis.
Includes statistics tracking for monitoring automation health.

Components:
- TriggerService: Business logic for trigger CRUD and execution management
  (uses chaoscypher_core.services.workflows.triggers.TriggerService directly)

Architecture:
Simplified VSA - uses engine TriggerService directly without wrapper layer.
Factory function in api.py provides dependency injection with storage adapter.

Example:
    from chaoscypher_core.services.workflows.triggers import TriggerService

    # Create auto-embedding trigger
    service = TriggerService(storage=adapter, database_name="default")
    trigger_id = service.create_trigger({
        "name": "Auto Embed",
        "workflow_id": workflow_id,
        "event_source": "document.committed"
    })

"""

# Re-export engine TriggerService for convenience
from chaoscypher_core.services.workflows.triggers import TriggerService
from chaoscypher_cortex.features.triggers.api import router


__all__ = [
    "TriggerService",
    "router",
]
