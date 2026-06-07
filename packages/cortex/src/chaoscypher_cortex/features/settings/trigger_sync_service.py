# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Trigger Sync Service.

Handles synchronization of system triggers with application settings.
Separated from SettingsService to follow Single Responsibility Principle.

Responsibilities:
- Sync auto-embedding triggers when enable_auto_embedding changes.
- Enable/disable system triggers based on settings.

Atomicity:
    sync_auto_embedding_triggers wraps all per-trigger writes in a single
    adapter.transaction() so mid-loop failures can't leave a mixed state.
"""

from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows import WorkflowService
    from chaoscypher_core.services.workflows.triggers import TriggerService

logger = structlog.get_logger(__name__)


class TriggerSyncService:
    """Service for synchronizing system triggers with application settings."""

    def __init__(
        self,
        trigger_service: TriggerService,
        workflow_service: WorkflowService,
        adapter: Any,
    ) -> None:
        """Initialize trigger sync service.

        Args:
            trigger_service: TriggerService instance for trigger management.
            workflow_service: WorkflowService instance for workflow lookups.
            adapter: Storage adapter that exposes a transaction() context
                manager. Used to wrap the whole sync in a single atomic
                block so partial failures roll back cleanly.
        """
        self.trigger_service = trigger_service
        self.workflow_service = workflow_service
        self.adapter = adapter

    def sync_auto_embedding_triggers(self, enabled: bool) -> int:
        """Sync auto-embedding triggers atomically.

        Args:
            enabled: Target enabled state for system auto-embedding triggers.

        Returns:
            Number of triggers synchronized.

        Raises:
            Exception: Propagates any per-trigger update failure so the
                transaction rolls back. Callers decide whether to retry
                or surface the error.
        """
        synced_count = 0
        with self.adapter.transaction():
            for event_source in ("node.created", "node.updated"):
                triggers = self.trigger_service.list_triggers(event_source=event_source)
                if not triggers:
                    continue
                workflow_ids = [t["workflow_id"] for t in triggers]
                workflows_by_id = {
                    w["id"]: w for w in self.workflow_service.list_workflows_by_ids(workflow_ids)
                }
                for trigger in triggers:
                    workflow = workflows_by_id.get(trigger["workflow_id"])
                    if not (workflow and workflow.get("is_system")):
                        continue
                    self.trigger_service.update_trigger(trigger["id"], {"enabled": enabled})
                    logger.info(
                        "system_trigger_updated",
                        trigger_name=trigger["name"],
                        trigger_id=trigger["id"],
                        enabled=enabled,
                    )
                    synced_count += 1

        if synced_count > 0:
            logger.info(
                "auto_embedding_triggers_synced",
                synced_count=synced_count,
                enable_auto_embedding=enabled,
            )
        return synced_count
