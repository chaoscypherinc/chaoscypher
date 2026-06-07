# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source recovery events audit-trail adapter mixin.

Records one row per real recovery dispatch by ``SourceRecovery`` and
exposes a per-source listing for the source detail UI's recovery
panel. Without this audit trail the "auto-recovered N times" warning
is opaque — operators have to grep logs to figure out whether the
recoveries were spurious or real.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.orm import load_only
from sqlmodel import desc, select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import SourceRecoveryEvent
from chaoscypher_core.utils.id import generate_id


logger = structlog.get_logger(__name__)


class SourceRecoveryEventsMixin(SqliteMixinBase):
    """Adds the recovery-events audit trail to the SQLite adapter."""

    def record_recovery_event(
        self,
        *,
        source_id: str,
        database_name: str,
        from_status: str,
        action_taken: str,
        reason: str,
        enqueued_count: int,
    ) -> None:
        """Append one event row for a real recovery dispatch.

        Best-effort: a failure here is logged but does not raise — the
        audit trail must not block the recovery work itself. Called by
        ``SourceRecovery._recover_one`` after a real dispatch (no-op
        debounced ticks do not write).

        Args:
            source_id: Source whose recovery is being recorded.
            database_name: Active database (multi-DB isolation).
            from_status: SourceRow.status when the classifier fired.
            action_taken: One of "extract_chunk", "import_commit",
                "index_document", "import_analysis", "finalize_extraction",
                or "compound" (multi-task dispatch).
            reason: Operator-readable reason. Today: "stalled" (default
                bulk reconcile path), "compound" (multi-chunk dispatch),
                "missing_queue_task" (source-level queue debounce miss).
            enqueued_count: Number of queue tasks actually enqueued. 1
                for single dispatch; >1 for compound; 0 for no-op (but
                the caller should not record a no-op).
        """
        self._ensure_connected()
        try:
            row = SourceRecoveryEvent(
                id=generate_id(prefix="rec"),
                source_id=source_id,
                database_name=database_name,
                attempt_at=datetime.now(UTC),
                from_status=from_status,
                action_taken=action_taken,
                reason=reason,
                enqueued_count=enqueued_count,
            )
            self.session.add(row)
            self._maybe_commit()
        except Exception as exc:
            logger.warning(
                "record_recovery_event_failed",
                source_id=source_id,
                database_name=database_name,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    def list_recovery_events(
        self,
        *,
        source_id: str,
        database_name: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return the most-recent recovery events for a source.

        Newest-first so the UI panel shows the most recent attempt at
        the top. Capped at ``limit`` rows; callers requesting more than
        the historical retention can iterate via offset (not yet
        implemented — current scale is well under the cap).

        Args:
            source_id: Source to fetch events for.
            database_name: Active database (multi-DB isolation).
            limit: Maximum rows to return. Default 50 covers the
                10-attempt exhaustion cap with a 5x margin.

        Returns:
            List of event dicts ordered ``attempt_at`` desc.
        """
        self._ensure_connected()
        statement = (
            select(SourceRecoveryEvent)
            .options(
                load_only(
                    SourceRecoveryEvent.id,
                    SourceRecoveryEvent.source_id,
                    SourceRecoveryEvent.database_name,
                    SourceRecoveryEvent.attempt_at,
                    SourceRecoveryEvent.from_status,
                    SourceRecoveryEvent.action_taken,
                    SourceRecoveryEvent.reason,
                    SourceRecoveryEvent.enqueued_count,
                )
            )
            .where(
                SourceRecoveryEvent.source_id == source_id,
                SourceRecoveryEvent.database_name == database_name,
            )
            .order_by(desc(SourceRecoveryEvent.attempt_at))
            .limit(limit)
        )
        rows = self.session.scalars(statement).all()
        return [
            {
                "id": r.id,
                "source_id": r.source_id,
                "database_name": r.database_name,
                "attempt_at": r.attempt_at,
                "from_status": r.from_status,
                "action_taken": r.action_taken,
                "reason": r.reason,
                "enqueued_count": r.enqueued_count,
            }
            for r in rows
        ]
