# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Trigger Domain Models.

Data structures for trigger execution and statistics.

Structure:
- entities.py: Internal domain models (TriggerExecutionStatus, TriggerExecution, TriggerStats)

Example:
    from chaoscypher_core.services.workflows.triggers.models import TriggerExecution, TriggerStats

    execution = TriggerExecution(
        execution_id="exec_123",
        trigger_id="trigger_456",
        trigger_name="On Node Created",
        workflow_id="wf_789",
        workflow_name="Process New Node",
        status=TriggerExecutionStatus.SUCCESS,
        event_source="node.created",
        fired_at=datetime.now(timezone.utc)
    )

"""

from chaoscypher_core.services.workflows.triggers.models.entities import (
    TriggerExecution,
    TriggerExecutionStatus,
    TriggerStats,
)


__all__ = [
    "TriggerExecution",
    "TriggerExecutionStatus",
    "TriggerStats",
]
