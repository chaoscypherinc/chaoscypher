# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Trigger Management - CRUD Operations and Statistics.

Provides CRUD operations for event triggers and execution statistics tracking.

Components:
- TriggerService: Create, read, update, delete triggers
- TriggerStatsTracker: Track trigger execution statistics and history

Example:
    from chaoscypher_core.services.workflows.triggers.management import (
        TriggerService,
        TriggerStatsTracker,
    )

    # Create service
    service = TriggerService(storage, database_name)
    triggers = service.list_triggers(enabled=True)

    # Track statistics
    tracker = TriggerStatsTracker(history_limit=100)

"""

from chaoscypher_core.services.workflows.triggers.management.service import TriggerService
from chaoscypher_core.services.workflows.triggers.management.stats_tracker import (
    TriggerStatsTracker,
)


__all__ = [
    "TriggerService",
    "TriggerStatsTracker",
]
