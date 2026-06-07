# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pause state repository — thin wrapper over the SQLite adapter.

Exists so the service layer never touches the adapter directly. Each
method is a one-line delegation that normalizes keyword arguments for
the adapter's source-pause / system-state methods from task 4.
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
    ) -> None:
        """Flip is_paused=True on a single source."""
        self.adapter.set_source_paused(
            source_id=source_id,
            database_name=database_name,
            is_paused=True,
            reason=reason,
        )

    def resume_source(
        self,
        *,
        source_id: str,
        database_name: str,
    ) -> None:
        """Flip is_paused=False and clear metadata on a single source."""
        self.adapter.set_source_paused(
            source_id=source_id,
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
