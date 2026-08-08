# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pause state repository — thin wrapper over the SQLite adapter.

Exists so the service layer never touches the adapter directly. Each
method is a one-line delegation that normalizes keyword arguments for
the adapter's source-pause / system-state methods.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter


class PauseRepository:
    """CRUD over SourceRow.is_paused and SystemState.processing_paused."""

    def __init__(self, adapter: SqliteAdapter) -> None:
        """Initialize with an SqliteAdapter (or compatible)."""
        self.adapter = adapter

    def pause_source(
        self,
        *,
        source_id: str,
        database_name: str,
        reason: str | None,
    ) -> int:
        """Flip is_paused=True on a single source.

        Returns the number of rows updated (0 when the source does not
        exist) so the service can 404 instead of reporting success for a
        deleted or mistyped source_id. Delegates to the bulk adapter
        method because it is the one that reports rowcount;
        ``set_source_paused`` writes the identical values but returns
        nothing.
        """
        return self.adapter.bulk_set_sources_paused(
            source_ids=[source_id],
            database_name=database_name,
            is_paused=True,
            reason=reason,
        )

    def resume_source(
        self,
        *,
        source_id: str,
        database_name: str,
    ) -> int:
        """Flip is_paused=False and clear metadata on a single source.

        Returns the number of rows updated (0 when the source does not
        exist) — see ``pause_source`` for the rationale.
        """
        return self.adapter.bulk_set_sources_paused(
            source_ids=[source_id],
            database_name=database_name,
            is_paused=False,
            reason=None,
        )

    def pause_sources(
        self,
        *,
        source_ids: list[str],
        database_name: str,
        reason: str | None,
    ) -> int:
        """Bulk-pause. Returns the number of rows updated."""
        return self.adapter.bulk_set_sources_paused(
            source_ids=source_ids,
            database_name=database_name,
            is_paused=True,
            reason=reason,
        )

    def resume_sources(
        self,
        *,
        source_ids: list[str],
        database_name: str,
    ) -> int:
        """Bulk-resume. Returns the number of rows updated."""
        return self.adapter.bulk_set_sources_paused(
            source_ids=source_ids,
            database_name=database_name,
            is_paused=False,
            reason=None,
        )

    def pause_system(
        self,
        *,
        reason: str | None,
        paused_by: str | None = None,
    ) -> None:
        """Flip the global processing_paused flag on."""
        self.adapter.set_system_paused(is_paused=True, reason=reason, paused_by=paused_by)

    def resume_system(self) -> None:
        """Flip the global processing_paused flag off."""
        self.adapter.set_system_paused(is_paused=False, reason=None, paused_by="user")

    def get_system_state(self) -> dict[str, Any]:
        """Read the singleton SystemState row (lazily created)."""
        return self.adapter.get_system_state()

    def list_system_events(
        self,
        *,
        event_type: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """List recent system events (audit trail), newest first."""
        return self.adapter.list_system_events(event_type=event_type, limit=limit)

    def clear_system_events(self) -> int:
        """Delete all system events. Returns the number deleted."""
        return self.adapter.clear_system_events()
