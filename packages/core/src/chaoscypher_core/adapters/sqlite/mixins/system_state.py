# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""System-wide state (pause/resume) adapter mixin.

Provides get_system_state / set_system_paused for the singleton
`system_state` table (one row, id=1) and a unified ``system_events``
audit trail. The table is created lazily on first read so fresh
databases don't need a seed step.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text, update
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import SystemEvent, SystemState


logger = structlog.get_logger(__name__)


def _decode_details(raw: str | None) -> dict[str, Any] | None:
    """Decode the JSON-encoded ``details`` payload stored on a system event.

    Writers (see ``EventBus.emit``) serialize a dict with ``json.dumps``
    before persisting; readers must reverse that so the API returns a
    structured object instead of an opaque string. Returns ``None`` if
    the column is empty or doesn't decode to an object.
    """
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):  # fmt: skip
        return None
    return decoded if isinstance(decoded, dict) else None


class SystemStateMixin(SqliteMixinBase):
    """Adds system-wide pause/resume operations to the SQLite adapter.

    Methods:
        get_system_state: Load or create the singleton state row.
        set_system_paused: Flip the global processing_paused flag.
        quick_check: Run a fast SQLite integrity check.
    """

    def get_system_state(self) -> dict[str, Any]:
        """Load the singleton SystemState row, creating it if absent.

        The row is keyed by id=1 and lazily upserted on first call,
        so callers never see a missing-state error.
        """
        self._ensure_connected()
        stmt = select(SystemState).where(SystemState.id == 1)
        row = self.session.scalars(stmt).first()
        if row is None:
            row = SystemState(id=1)
            self.session.add(row)
            self._maybe_commit()
            self.session.refresh(row)
        return {
            "id": row.id,
            "processing_paused": row.processing_paused,
            "processing_paused_at": row.processing_paused_at,
            "processing_paused_reason": row.processing_paused_reason,
            "paused_by": row.paused_by,
        }

    def set_system_paused(
        self,
        *,
        is_paused: bool,
        reason: str | None = None,
        paused_by: str | None = None,
    ) -> None:
        """Set or clear the system-wide processing-paused flag.

        Ensures the singleton row exists before issuing the UPDATE so
        the first system-pause call on a fresh database still succeeds.
        """
        self._ensure_connected()
        self.get_system_state()  # ensure singleton row exists

        # Capture the event source BEFORE clearing paused_by in the
        # update values.  On resume the caller should pass paused_by
        # (e.g. "user"), but as a safety net we fall back to the
        # currently-stored paused_by so the audit trail always records
        # who triggered the action.
        event_source = paused_by
        if not is_paused and event_source is None:
            current = self.get_system_state()
            event_source = current.get("paused_by")

        values: dict[str, Any] = {"processing_paused": is_paused}
        if is_paused:
            values["processing_paused_at"] = datetime.now(UTC)
            values["processing_paused_reason"] = reason
            values["paused_by"] = paused_by
        else:
            values["processing_paused_at"] = None
            values["processing_paused_reason"] = None
            values["paused_by"] = None

        stmt = update(SystemState).where(SystemState.id == 1).values(**values)
        self.session.execute(stmt)
        self._maybe_commit()

        # Audit trail
        self.record_system_event(
            event_type="pause" if is_paused else "resume",
            action="System processing paused" if is_paused else "System processing resumed",
            source=event_source,
            reason=reason,
        )

    def record_system_event(
        self,
        *,
        event_type: str,
        action: str,
        source: str | None = None,
        reason: str | None = None,
        details: str | None = None,
        database_name: str | None = None,
        max_events: int = 100,
    ) -> None:
        """Insert a system event and prune old rows.

        Args:
            event_type: Event category (``"pause"``, ``"resume"``,
                ``"health_change"``, ``"task_failed"``, ``"recovery"``).
            action: Human-readable action description.
            source: Who/what triggered the event (``"user"``,
                ``"health_monitor"``, ``"reconciler"``, ``"worker"``).
            reason: Human-readable reason string.
            details: JSON string with extra context (probe names,
                task info, etc.).
            database_name: Scoped database name, if applicable.
            max_events: Maximum rows to retain. Oldest rows beyond
                this limit are deleted after each insert.
        """
        self._ensure_connected()

        event = SystemEvent(
            type=event_type,
            action=action,
            source=source,
            reason=reason,
            details=details,
            database_name=database_name,
        )
        self.session.add(event)
        self._maybe_commit()

        # Prune old rows if count exceeds max_events
        count_result = self.session.execute(text("SELECT COUNT(*) FROM system_events"))
        total = count_result.scalar() or 0
        if total > max_events:
            self.session.execute(
                text(
                    "DELETE FROM system_events WHERE id NOT IN "
                    "(SELECT id FROM system_events "
                    "ORDER BY timestamp DESC LIMIT :keep)"
                ),
                {"keep": max_events},
            )
            self._maybe_commit()
            logger.debug(
                "system_events_pruned",
                deleted=total - max_events,
                remaining=max_events,
            )

    def list_system_events(
        self,
        *,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return the most recent system events.

        Args:
            event_type: Optional filter by event type (e.g. ``"pause"``).
                When ``None``, all event types are returned.
            limit: Maximum number of rows to return (newest first).

        Returns:
            List of dicts with keys: id, timestamp, type, action,
            source, reason, details, database_name.
        """
        self._ensure_connected()
        # No load_only(): the return dict includes all columns (including
        # details), and the table is pruned to max 100 rows so ORM
        # overhead is negligible.
        stmt = select(SystemEvent).order_by(
            SystemEvent.timestamp.desc()  # type: ignore[union-attr]
        )
        if event_type is not None:
            stmt = stmt.where(SystemEvent.type == event_type)
        stmt = stmt.limit(limit)
        rows = self.session.exec(stmt).all()
        return [
            {
                "id": row.id,
                "timestamp": (
                    row.timestamp.isoformat() + "Z"
                    if row.timestamp and row.timestamp.tzinfo is None
                    else row.timestamp.isoformat()
                    if row.timestamp
                    else None
                ),
                "type": row.type,
                "action": row.action,
                "source": row.source,
                "reason": row.reason,
                "details": _decode_details(row.details),
                "database_name": row.database_name,
            }
            for row in rows
        ]

    def clear_system_events(self) -> int:
        """Delete all rows from the system_events table.

        Returns:
            Number of rows deleted.
        """
        self._ensure_connected()
        count_result = self.session.execute(text("SELECT COUNT(*) FROM system_events"))
        total = count_result.scalar() or 0
        if total > 0:
            self.session.execute(text("DELETE FROM system_events"))
            self._maybe_commit()
            logger.info("system_events_cleared", deleted=total)
        return total

    def quick_check(self) -> bool:
        """Verify the database is accessible and responsive.

        Runs a simple ``SELECT 1`` to confirm the connection works.
        This is intentionally lightweight — ``PRAGMA quick_check``
        inspects B-tree page integrity which can false-fail during
        concurrent multi-process writes (Cortex + Neuron).

        Returns:
            True if the database responds to a query, False otherwise.
        """
        self._ensure_connected()
        try:
            result = self.session.execute(text("SELECT 1"))
            row = result.fetchone()
            return row is not None and row[0] == 1
        except Exception:
            logger.warning("quick_check_failed", exc_info=True)
            return False

    def writable_check(self) -> bool:
        """Verify the database accepts writes via BEGIN IMMEDIATE + ROLLBACK.

        ``BEGIN IMMEDIATE`` attempts to acquire the RESERVED lock that a real
        write would need.  On a read-only-mounted database SQLite raises
        ``OperationalError: attempt to write a readonly database``.  The
        follow-up ``ROLLBACK`` releases the lock without persisting any
        change, so the call is idempotent and side-effect-free.

        A fresh raw connection (not the shared ORM session) is used so the
        explicit transaction control statements do not interfere with the
        session's own transaction state.

        Returns:
            True if the database accepts writes.

        Raises:
            OperationalError: if the database is read-only or otherwise
                rejects the write lock.
        """
        self._ensure_connected()
        engine = self.session.bind
        with engine.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            conn.exec_driver_sql("ROLLBACK")
        return True
