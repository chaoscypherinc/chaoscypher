# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""TriggerStorageProtocol — storage contract for workflow triggers.

Split from the legacy ``ports/storage.py`` god file on 2026-04-23.
Implemented by ``chaoscypher_core.adapters.sqlite.mixins.triggers.TriggersMixin``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from chaoscypher_core.ports.types import TriggerDict


@runtime_checkable
class TriggerStorageProtocol(Protocol):
    """Storage protocol for trigger operations.

    Handles CRUD for:
    - Triggers (event triggers)
    - Trigger executions (history)
    """

    # Triggers
    def get_trigger(self, trigger_id: str, database_name: str) -> TriggerDict | None:
        """Get trigger by ID and database."""
        ...

    def create_trigger(self, trigger: dict[str, Any]) -> TriggerDict:
        """Create trigger."""
        ...

    def update_trigger(self, trigger_id: str, updates: dict[str, Any]) -> TriggerDict:
        """Update trigger."""
        ...

    def delete_trigger(self, trigger_id: str) -> bool:
        """Delete trigger."""
        ...

    def list_triggers(
        self,
        database_name: str,
        event_source: str | None = None,
        enabled: bool | None = None,
    ) -> list[TriggerDict]:
        """List triggers for database with optional filters."""
        ...

    # Trigger Executions
    def get_executions(self, trigger_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent executions for a trigger."""
        ...

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 10).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def count_triggers(self, *, database_name: str) -> int:
        """Count Trigger rows in one database."""
        ...

    def delete_all_triggers(self, *, database_name: str) -> int:
        """Delete every Trigger row in one database. Returns count."""
        ...

    def clear_all_trigger_executions(self) -> int:
        """Delete every TriggerExecutionRow across databases. Returns count."""
        ...
