# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Trigger Service - Hexagonal Architecture trigger management.

Manages CRUD operations for event triggers for workflows.
Uses storage protocol for backend-independent data access.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import TriggerValidationError
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.ports.storage_triggers import TriggerStorageProtocol
    from chaoscypher_core.ports.types import TriggerDict

logger = structlog.get_logger(__name__)

# Filter validation bounds
_FILTERS_MAX_DEPTH = 5
_FILTERS_MAX_BYTES = 16 * 1024  # 16 KB
_FILTERS_MAX_KEYS_PER_LEVEL = 50


def _validate_filters(filters: dict[str, Any] | None) -> None:
    """Validate trigger filter dict against static bounds.

    Bounds:
        * Max nesting depth: 5.
        * Max serialized JSON size: 16 KB.
        * Max keys per level: 50.

    Matching semantics:
        TriggerExecutor._filters_match uses literal equality at the top level
        only — no globs, no regex, no nested traversal. Nested values exist
        for storage fidelity, but the executor compares only top-level keys.

    Args:
        filters: The filter dict to validate. None or {} are accepted.

    Raises:
        TriggerValidationError: If any bound is violated.
    """
    if not filters:
        return

    try:
        serialized = json.dumps(filters, default=str)
    except (TypeError, ValueError) as exc:
        msg = f"filters must be JSON-serializable: {exc}"
        raise TriggerValidationError(msg, field="filters") from exc

    if len(serialized.encode("utf-8")) > _FILTERS_MAX_BYTES:
        msg = f"filters exceeds {_FILTERS_MAX_BYTES} bytes serialized"
        raise TriggerValidationError(msg, field="filters")

    def _walk(node: Any, depth: int) -> None:
        # Only composite nodes (dict/list) count toward the depth budget.
        # Leaf values (strings, numbers, bool, None) terminate the walk.
        if isinstance(node, dict):
            if depth > _FILTERS_MAX_DEPTH:
                msg = f"filters depth exceeds {_FILTERS_MAX_DEPTH}"
                raise TriggerValidationError(msg, field="filters")
            if len(node) > _FILTERS_MAX_KEYS_PER_LEVEL:
                msg = f"filters has >{_FILTERS_MAX_KEYS_PER_LEVEL} keys at one level"
                raise TriggerValidationError(msg, field="filters")
            for v in node.values():
                _walk(v, depth + 1)
        elif isinstance(node, list):
            if depth > _FILTERS_MAX_DEPTH:
                msg = f"filters depth exceeds {_FILTERS_MAX_DEPTH}"
                raise TriggerValidationError(msg, field="filters")
            for item in node:
                _walk(item, depth + 1)

    _walk(filters, 0)


class TriggerService:
    """Service for managing event triggers (CRUD operations).

    Example:
        >>> from chaoscypher_core.services.workflows.triggers.api import get_trigger_service
        >>> from chaoscypher_core.adapters.sqlite import get_db_session
        >>> from chaoscypher_core.settings import EngineSettings
        >>>
        >>> # Get service instance via factory
        >>> settings = EngineSettings()
        >>> with get_db_session("my_database") as session:
        ...     settings = get_settings()
        ...     service = get_trigger_service(session, settings)
        ...
        ...     # Create a new trigger.
        ...     #
        ...     # NOTE on `filters`: the executor matches filter keys against
        ...     # the event payload via top-level literal equality (see
        ...     # ``TriggerExecutor._filters_match``). Filter keys that are
        ...     # not present in the event payload silently never match — the
        ...     # trigger never fires. Today's only published events are
        ...     # ``node.create`` / ``edge.create`` with payload keys
        ...     # ``entity_type`` and ``entity_id`` (see
        ...     # ``operations/importing/import_service.py``); pick filter
        ...     # keys from that set, or use ``filters={}`` to fire on every
        ...     # event of the given source.
        ...     trigger_id = service.create_trigger({
        ...         "name": "Embed every newly-created node",
        ...         "event_source": "node.create",
        ...         "workflow_id": "wf_analysis_123",
        ...         "filters": {"entity_type": "node"},
        ...         "enabled": True,
        ...     })
        ...     print(trigger_id)
        ...     "tr_abc123"
        ...
        ...     # List triggers for a workflow
        ...     triggers = service.list_triggers(workflow_id="wf_analysis_123", enabled=True)
        ...     print(len(triggers))
        ...     1
        ...
        ...     # Toggle trigger state
        ...     service.toggle_trigger(trigger_id, enabled=False)

    """

    def __init__(self, storage: TriggerStorageProtocol, database_name: str):
        """Initialize trigger service.

        Args:
            storage: TriggerStorageProtocol instance
            database_name: Database name for filtering

        """
        self.storage = storage
        self.database_name = database_name

    # ========================================================================
    # Trigger CRUD
    # ========================================================================

    def list_triggers(
        self,
        event_source: str | None = None,
        workflow_id: str | None = None,
        enabled: bool | None = None,
        user_id: int | None = None,
    ) -> list[TriggerDict]:
        """List triggers with optional filters, sorted by priority DESC, created_at ASC.

        Dispatch order semantics:
            * Higher ``priority`` values fire first.
            * Ties broken by ``created_at`` ASC (older triggers before newer).

        Args:
            event_source: Filter by event source.
            workflow_id: Filter by workflow ID.
            enabled: Filter by enabled flag.
            user_id: Filter by owner user_id. None means no user filter
                (system triggers + all users visible, used when auth disabled).

        Returns:
            List of trigger dictionaries sorted for dispatch.
        """
        triggers = self.storage.list_triggers(
            database_name=self.database_name, event_source=event_source, enabled=enabled
        )
        # Apply workflow_id filter in service (storage protocol doesn't support it)
        if workflow_id is not None:
            triggers = [t for t in triggers if t.get("workflow_id") == workflow_id]

        # user_id filter (None means "don't filter" — used in single-user mode)
        if user_id is not None:
            triggers = [
                t for t in triggers if t.get("user_id") == user_id or t.get("user_id") is None
            ]

        # Dispatch order: priority DESC, created_at ASC (stable tie-break).
        # created_at is stored as ISO-8601 str on TriggerDict; lexicographic
        # comparison preserves chronological order for any well-formed UTC value.
        min_ts = datetime.min.replace(tzinfo=UTC).isoformat()

        def _sort_key(t: TriggerDict) -> tuple[int, str]:
            prio = t.get("priority") or 0
            ts = t.get("created_at") or min_ts
            return (-prio, ts)

        triggers.sort(key=_sort_key)
        return triggers

    def get_trigger(self, trigger_id: str) -> TriggerDict | None:
        """Get trigger by ID.

        Args:
            trigger_id: Trigger ID

        Returns:
            Trigger dictionary or None

        """
        return self.storage.get_trigger(trigger_id, self.database_name)

    def create_trigger(self, trigger_data: dict[str, Any]) -> str:
        """Create a new trigger.

        Args:
            trigger_data: Trigger data dictionary

        Returns:
            Created trigger ID

        """
        _validate_filters(trigger_data.get("filters"))
        trigger_id = trigger_data.get("id", generate_id())

        trigger_dict = {
            "id": trigger_id,
            "database_name": self.database_name,
            "name": trigger_data["name"],
            "event_source": trigger_data["event_source"],
            "filters": trigger_data.get("filters", {}),
            "workflow_id": trigger_data["workflow_id"],
            "workflow_inputs": trigger_data.get("workflow_inputs"),
            "enabled": trigger_data.get("enabled", True),
            "priority": trigger_data.get("priority", 0),
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }

        created = self.storage.create_trigger(trigger_dict)

        logger.info(
            "trigger_created",
            trigger_id=created["id"],
            trigger_name=created["name"],
            event_source=created["event_source"],
            workflow_id=created["workflow_id"],
        )
        return created["id"]

    def update_trigger(self, trigger_id: str, updates: dict[str, Any]) -> bool:
        """Update trigger.

        Args:
            trigger_id: Trigger ID
            updates: Dictionary of fields to update

        Returns:
            True if updated, False if not found

        """
        if "filters" in updates:
            _validate_filters(updates["filters"])

        trigger = self.storage.get_trigger(trigger_id, self.database_name)
        if not trigger:
            return False

        # Prepare updates with timestamp
        update_dict = {
            k: v
            for k, v in updates.items()
            if k
            in [
                "name",
                "event_source",
                "filters",
                "workflow_id",
                "workflow_inputs",
                "enabled",
                "priority",
            ]
        }
        update_dict["updated_at"] = datetime.now(UTC)

        self.storage.update_trigger(trigger_id, update_dict)

        logger.info(
            "trigger_updated",
            trigger_id=trigger_id,
            trigger_name=trigger.get("name"),
            updated_fields=list(updates.keys()),
        )
        return True

    def delete_trigger(self, trigger_id: str) -> bool:
        """Delete trigger.

        Args:
            trigger_id: Trigger ID

        Returns:
            True if deleted, False if not found

        """
        trigger = self.storage.get_trigger(trigger_id, self.database_name)
        if not trigger:
            return False

        self.storage.delete_trigger(trigger_id)

        logger.info("trigger_deleted", trigger_id=trigger_id, trigger_name=trigger.get("name"))
        return True

    def toggle_trigger(self, trigger_id: str, enabled: bool) -> bool:
        """Enable or disable a trigger.

        Args:
            trigger_id: Trigger ID
            enabled: New enabled state

        Returns:
            True if updated, False if not found

        """
        return self.update_trigger(trigger_id, {"enabled": enabled})

    def get_trigger_stats(self, trigger_id: str, *, history_limit: int = 1000) -> dict[str, Any]:
        """Compute aggregate statistics for a trigger from its execution history.

        Args:
            trigger_id: Trigger ID.
            history_limit: Max executions to scan (most recent first).

        Returns:
            Dict with ``total_executions``, ``successful_executions``,
            ``failed_executions``, ``success_rate`` (0.0-1.0), and
            ``average_duration_ms`` (placeholder 0; per-execution duration is
            not yet persisted on the trigger_executions row).
        """
        executions = self.storage.get_executions(trigger_id, limit=history_limit)
        total = len(executions)
        successful = sum(1 for e in executions if e.get("status") == "success")
        failed = total - successful
        success_rate = (successful / total) if total else 0.0
        return {
            "total_executions": total,
            "successful_executions": successful,
            "failed_executions": failed,
            "success_rate": success_rate,
            "average_duration_ms": 0,
        }
