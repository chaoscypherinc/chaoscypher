# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Trigger Storage Protocol Mixin for SqliteAdapter."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import load_only
from sqlmodel import delete, select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import Trigger, TriggerExecutionRow
from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.ports.storage_triggers import TriggerStorageProtocol


class TriggersMixin(SqliteMixinBase, TriggerStorageProtocol):
    """Mixin implementing TriggerStorageProtocol for SQLite storage.

    Implements operations for:
    - Triggers
    - Trigger executions
    """

    def get_trigger(self, trigger_id: str, database_name: str) -> dict[str, Any] | None:
        """Get trigger by ID and database."""
        self._ensure_connected()
        trigger = self.session.get(Trigger, trigger_id)
        if trigger and trigger.database_name == database_name:
            return self._entity_to_dict(trigger)
        return None

    def create_trigger(self, trigger_data: dict[str, Any]) -> dict[str, Any]:
        """Create trigger."""
        self._ensure_connected()
        trigger = Trigger(**trigger_data)
        self.session.add(trigger)
        self._maybe_commit()
        self.session.refresh(trigger)
        return self._entity_to_dict(trigger)

    def update_trigger(self, trigger_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update trigger."""
        self._ensure_connected()
        trigger = self.session.get(Trigger, trigger_id)
        if not trigger:
            msg = "Trigger"
            raise NotFoundError(msg, trigger_id)

        for key, value in updates.items():
            setattr(trigger, key, value)

        trigger.updated_at = datetime.now(UTC)
        self.session.add(trigger)
        self._maybe_commit()
        self.session.refresh(trigger)
        return self._entity_to_dict(trigger)

    def delete_trigger(self, trigger_id: str) -> bool:
        """Delete trigger."""
        self._ensure_connected()
        trigger = self.session.get(Trigger, trigger_id)
        if not trigger:
            return False

        self.session.delete(trigger)
        self._maybe_commit()
        return True

    def list_triggers(
        self,
        database_name: str,
        event_source: str | None = None,
        enabled: bool | None = None,
    ) -> list[dict[str, Any]]:
        """List triggers for database with optional filters."""
        self._ensure_connected()
        stmt = (
            select(Trigger)
            .options(
                load_only(
                    Trigger.id,
                    Trigger.database_name,
                    Trigger.user_id,
                    Trigger.name,
                    Trigger.event_source,
                    Trigger.workflow_id,
                    Trigger.enabled,
                    Trigger.priority,
                    Trigger.created_at,
                    Trigger.updated_at,
                    # EXCLUDE: filters, workflow_inputs (JSON)
                )
            )
            .where(Trigger.database_name == database_name)
        )

        if event_source is not None:
            stmt = stmt.where(Trigger.event_source == event_source)
        if enabled is not None:
            stmt = stmt.where(Trigger.enabled == enabled)

        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    def get_executions(self, trigger_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent executions for a trigger."""
        self._ensure_connected()
        stmt = (
            select(TriggerExecutionRow)
            .options(
                load_only(
                    TriggerExecutionRow.id,
                    TriggerExecutionRow.trigger_id,
                    TriggerExecutionRow.workflow_execution_id,
                    TriggerExecutionRow.status,
                    TriggerExecutionRow.error_message,
                    TriggerExecutionRow.executed_at,
                )
            )
            .where(TriggerExecutionRow.trigger_id == trigger_id)
            .order_by(TriggerExecutionRow.executed_at.desc())
            .limit(limit)
        )
        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 10).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def count_triggers(self, *, database_name: str) -> int:
        """Count Trigger rows in one database."""
        self._ensure_connected()
        stmt = (
            select(func.count()).select_from(Trigger).where(Trigger.database_name == database_name)
        )
        return int(self.session.exec(stmt).one())

    def delete_all_triggers(self, *, database_name: str) -> int:
        """Delete every Trigger row in one database."""
        self._ensure_connected()
        stmt = delete(Trigger).where(Trigger.database_name == database_name)
        result = self.session.exec(stmt)
        self._maybe_commit()
        return int(result.rowcount or 0)

    def clear_all_trigger_executions(self) -> int:
        """Delete every TriggerExecutionRow across databases."""
        self._ensure_connected()
        result = self.session.exec(delete(TriggerExecutionRow))
        self._maybe_commit()
        return int(result.rowcount or 0)
