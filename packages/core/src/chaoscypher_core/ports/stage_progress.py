# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""StageProgressStorageProtocol — universal stage-progress storage seam.

The v1 implementation is source-keyed (``parent_id`` is a source ID,
backed by the ``llm_stage_progress`` table). Future parent types
(workflow_executions, etc.) implement this same protocol against
their own backing store without changes to the helper or any caller.

Implemented by ``chaoscypher_core.adapters.sqlite.mixins.stage_progress.StageProgressMixin``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from datetime import datetime


@runtime_checkable
class StageProgressStorageProtocol(Protocol):
    """Three lifecycle methods plus an extras side channel.

    All methods are async + keyword-only to match the existing port style.
    Implementations are best-effort consumers — failures are caught and
    logged by the ``StageProgress`` helper, not propagated to callers.
    """

    async def start_stage(
        self,
        *,
        parent_id: str,
        stage_name: str,
        total: int,
        started_at: datetime,
    ) -> None:
        """UPSERT the stage row.

        Idempotent: re-calling for the same (parent_id, stage_name) zeros
        processed/avg_ms and clears completed_at.
        """
        ...

    async def tick_stage(
        self,
        *,
        parent_id: str,
        stage_name: str,
        processed: int,
        avg_ms: int | None,
        last_activity: datetime,
    ) -> None:
        """Update progress for a started stage.

        No-op if start_stage was never called for this (parent_id, stage_name).
        """
        ...

    async def complete_stage(
        self,
        *,
        parent_id: str,
        stage_name: str,
        completed_at: datetime,
    ) -> None:
        """Mark the stage complete. Final state preserved on the row."""
        ...

    async def update_stage_extras(
        self,
        *,
        parent_id: str,
        stage_name: str,
        extras: dict[str, Any] | None,
        last_activity: datetime,
    ) -> None:
        """Side channel for stage-specific extension data.

        Entries are shown in the UI tooltip's extras slot. MCP extraction uses
        this for live entity/relationship preview counts. Vision and embedding
        leave it unused. The helper context manager doesn't call it — only
        direct port callers do.
        """
        ...
