# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow execution status enum.

Lives in runtime so the orchestrator (which reads/writes status during
execution) and the cortex API DTOs (which serialize status in HTTP
responses) can both import from a neutral location.
"""

from enum import StrEnum


class WorkflowExecutionStatus(StrEnum):
    """Status values for a workflow execution row."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


__all__ = ["WorkflowExecutionStatus"]
