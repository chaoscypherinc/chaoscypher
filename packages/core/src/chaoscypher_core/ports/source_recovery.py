# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Composite Port consumed by SourceRecovery.

Bundles the storage surface ``SourceRecovery`` needs into one Protocol so
the service depends on a single abstraction instead of the concrete
``SqliteAdapter`` class. Structural typing means no explicit inheritance
is required — ``SqliteAdapter`` satisfies this Protocol automatically
once every listed method is implemented on its mixins.

Used by
``packages/core/src/chaoscypher_core/services/sources/recovery.py``
once the service is rewired in PR2c Task 27 to take
``adapter: SourceRecoveryPorts`` instead of the concrete adapter.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from datetime import datetime

    from chaoscypher_core.vision.states import VisionPageStatus


@runtime_checkable
class SourceRecoveryPorts(Protocol):
    """Storage operations required by ``SourceRecovery``."""

    # --- SourceStorageProtocol subset ---

    def get_source(self, source_id: str, database_name: str = "") -> dict[str, Any] | None:
        """Fetch a single source by ID."""
        ...

    def count_sources_by_statuses(self, *, statuses: list[str], database_name: str) -> int:
        """COUNT sources whose status is in the given set (no row materialization)."""
        ...

    def list_sources_by_statuses(
        self, *, statuses: list[str], database_name: str
    ) -> list[dict[str, Any]]:
        """List sources whose status is in the given set."""
        ...

    # --- System state ---

    def get_system_state(self) -> dict[str, Any]:
        """Return the singleton ``SystemState`` row (global pause flag etc.)."""
        ...

    # --- Recovery-tracking mutators ---

    def increment_source_recovery_attempts(self, *, source_id: str, database_name: str) -> None:
        """Add 1 to the source's ``recovery_attempts`` counter."""
        ...

    def reset_source_recovery_attempts(self, *, source_id: str, database_name: str) -> None:
        """Zero the source's ``recovery_attempts`` counter.

        Called by handlers on entry to a NEW non-terminal stage (e.g.,
        finalize_extraction sets status='extracted', commit sets
        status='committing'). A successful stage transition proves the
        source made forward progress, so accumulated false-positive
        recoveries from the prior stage must not carry over and
        compound toward the exhaustion cap.
        """
        ...

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
        """Append one row to the recovery audit trail.

        Called by ``SourceRecovery._recover_one`` after a real (non
        no-op) dispatch. Surfaces in the source detail UI's recovery
        events panel. Best-effort; failures are logged and suppressed.
        """
        ...

    def update_source_last_activity(
        self, *, source_id: str, database_name: str, at_time: datetime
    ) -> None:
        """Set the source's ``last_activity_at`` to the given time."""
        ...

    def mark_source_exhausted(
        self,
        source_id: str,
        database_name: str,
        error_message: str,
    ) -> None:
        """Transition the source to the terminal recovery-exhausted state."""
        ...

    # --- Extraction queue queries ---

    def get_active_extraction_job(
        self, *, source_id: str, database_name: str
    ) -> dict[str, Any] | None:
        """Return the non-terminal ``ChunkExtractionJob`` for a source, if any."""
        ...

    def list_extraction_tasks_by_status(
        self,
        *,
        job_id: str,
        statuses: list[str],
        database_name: str,
    ) -> list[dict[str, Any]]:
        """List ``ChunkExtractionTask`` rows for a job whose status is in the set."""
        ...

    def list_source_entities(self, source_id: str, database_name: str) -> list[dict[str, Any]]:
        """Return every persisted entity for a source, in extraction order."""
        ...

    def list_source_relationships(self, source_id: str, database_name: str) -> list[dict[str, Any]]:
        """Return every persisted relationship for a source, in extraction order."""
        ...

    def get_source_commit_payload(
        self, source_id: str, database_name: str
    ) -> dict[str, Any] | None:
        """Return the persisted commit payload stashed by the extraction finalizer.

        Contains entities, relationships, suggested templates, etc. Returns
        ``None`` if no payload is pending.
        """
        ...

    def set_source_commit_payload(
        self,
        source_id: str,
        payload: dict[str, Any],
        database_name: str,
    ) -> None:
        """Persist the commit payload on the source row before enqueueing.

        Required by ``_dispatch_commit`` (audit fix #C4) to write the
        large ``commit_data`` dict to the DB column BEFORE the queue
        message lands. The commit handler reads the payload from here,
        not from the queue message.
        """
        ...

    # --- Vision queue queries ---

    def get_vision_job_by_source(self, source_id: str) -> dict[str, Any] | None:
        """Return the most recent vision_jobs row for a source, or None."""
        ...

    def list_vision_page_descriptions(
        self,
        source_id: str,
        *,
        statuses: Sequence[VisionPageStatus] | None = None,
    ) -> list[dict[str, Any]]:
        """Return all vision_page_descriptions rows for a source, filtered by status."""
        ...
