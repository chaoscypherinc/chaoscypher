# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Trigger System - Event-Driven Workflow Automation.

Provides event-driven workflow trigger execution with CRUD management.

Architecture:
- management/: CRUD operations and statistics (TriggerService, TriggerStatsTracker)
- engine/: Execution engine (TriggerExecutor)
- models/: Domain models (TriggerExecutionStatus, TriggerExecution, TriggerStats)

Example:
    from chaoscypher_core.services.workflows.triggers import (
        TriggerService,
        TriggerExecutor,
        TriggerStatsTracker,
        TriggerExecution,
        TriggerStats,
    )

    # Manage triggers
    service = TriggerService(storage, database_name)
    triggers = service.list_triggers(workflow_id="wf_123")

    # Execute triggers
    executor = TriggerExecutor(
        trigger_service=service,
        workflow_service=workflow_svc,
        ...
    )
    await executor.dispatch_event("node.create", data)

"""

# Management: CRUD and statistics
# Engine: Execution
from chaoscypher_core.services.workflows.triggers.engine import TriggerExecutor
from chaoscypher_core.services.workflows.triggers.management import (
    TriggerService,
    TriggerStatsTracker,
)

# Models: Domain models
from chaoscypher_core.services.workflows.triggers.models import (
    TriggerExecution,
    TriggerExecutionStatus,
    TriggerStats,
)


__all__ = [
    "TriggerExecution",
    # Models
    "TriggerExecutionStatus",
    # Engine
    "TriggerExecutor",
    # Management
    "TriggerService",
    "TriggerStats",
    "TriggerStatsTracker",
]
