# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""StageProgressMixin — implements StageProgressStorageProtocol against ``llm_stage_progress``.

All four methods are scoped to (source_id, stage_name) — v1 ``parent_id``
IS a source id. Future parent types get their own mixin against their
own table.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import text


if TYPE_CHECKING:
    from datetime import datetime

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.ports.stage_progress import StageProgressStorageProtocol
from chaoscypher_core.ports.types import StageProgressDict


if TYPE_CHECKING:
    from datetime import datetime


class StageProgressMixin(SqliteMixinBase, StageProgressStorageProtocol):
    """Adapter implementation of StageProgressStorageProtocol."""

    async def start_stage(
        self,
        *,
        parent_id: str,
        stage_name: str,
        total: int,
        started_at: datetime,
    ) -> None:
        """UPSERT — idempotent re-start zeros processed/avg_ms/completed_at."""
        self._ensure_connected()
        self.session.execute(
            text("""
            INSERT INTO llm_stage_progress (
                source_id, stage_name, total, processed, avg_ms,
                started_at, last_activity, completed_at
            ) VALUES (
                :sid, :stage, :total, 0, NULL, :started, :started, NULL
            )
            ON CONFLICT (source_id, stage_name) DO UPDATE SET
                total = excluded.total,
                processed = 0,
                avg_ms = NULL,
                started_at = excluded.started_at,
                last_activity = excluded.last_activity,
                completed_at = NULL
        """),
            {
                "sid": parent_id,
                "stage": stage_name,
                "total": total,
                "started": started_at,
            },
        )
        self._maybe_commit()

    async def tick_stage(
        self,
        *,
        parent_id: str,
        stage_name: str,
        processed: int,
        avg_ms: int | None,
        last_activity: datetime,
    ) -> None:
        """UPDATE. No-op if the row doesn't exist (best-effort)."""
        self._ensure_connected()
        self.session.execute(
            text("""
            UPDATE llm_stage_progress SET
                processed = :p, avg_ms = :a, last_activity = :la
            WHERE source_id = :sid AND stage_name = :stage
        """),
            {
                "p": processed,
                "a": avg_ms,
                "la": last_activity,
                "sid": parent_id,
                "stage": stage_name,
            },
        )
        self._maybe_commit()

    async def complete_stage(
        self,
        *,
        parent_id: str,
        stage_name: str,
        completed_at: datetime,
    ) -> None:
        """Set completed_at and update last_activity on the progress row."""
        self._ensure_connected()
        self.session.execute(
            text("""
            UPDATE llm_stage_progress SET
                completed_at = :ct, last_activity = :ct
            WHERE source_id = :sid AND stage_name = :stage
        """),
            {"ct": completed_at, "sid": parent_id, "stage": stage_name},
        )
        self._maybe_commit()

    async def update_stage_extras(
        self,
        *,
        parent_id: str,
        stage_name: str,
        extras: dict[str, Any] | None,
        last_activity: datetime,
    ) -> None:
        """Write extras_json on an existing row.

        No-op if row absent — MCP RPC handlers always call start_stage first.
        """
        self._ensure_connected()
        payload = json.dumps(extras) if extras is not None else None
        self.session.execute(
            text("""
            UPDATE llm_stage_progress SET
                extras_json = :ej, last_activity = :la
            WHERE source_id = :sid AND stage_name = :stage
        """),
            {
                "ej": payload,
                "la": last_activity,
                "sid": parent_id,
                "stage": stage_name,
            },
        )
        self._maybe_commit()

    def _fetch_stage_progress(self, source_id: str) -> dict[str, StageProgressDict]:
        """Read all stage rows for one source. Used by get_source()."""
        self._ensure_connected()
        rows = self.session.execute(
            text("""
            SELECT stage_name, total, processed, avg_ms,
                   started_at, last_activity, completed_at, extras_json
            FROM llm_stage_progress WHERE source_id = :sid
        """),
            {"sid": source_id},
        ).all()
        return {
            row.stage_name: StageProgressDict(
                total=row.total,
                processed=row.processed,
                avg_ms=row.avg_ms,
                started_at=row.started_at,
                last_activity=row.last_activity,
                completed_at=row.completed_at,
                extras=json.loads(row.extras_json) if row.extras_json is not None else None,
            )
            for row in rows
        }

    def _fetch_stage_progress_bulk(
        self,
        source_ids: list[str],
    ) -> dict[str, dict[str, StageProgressDict]]:
        """Read stage rows for many sources in one query. Used by list_sources()."""
        if not source_ids:
            return {}
        self._ensure_connected()
        placeholders = ",".join(f":id{i}" for i in range(len(source_ids)))
        params = {f"id{i}": sid for i, sid in enumerate(source_ids)}
        rows = self.session.execute(
            text(f"""
            SELECT source_id, stage_name, total, processed, avg_ms,
                   started_at, last_activity, completed_at, extras_json
            FROM llm_stage_progress
            WHERE source_id IN ({placeholders})
        """),
            params,
        ).all()
        out: dict[str, dict[str, StageProgressDict]] = {}
        for row in rows:
            out.setdefault(row.source_id, {})[row.stage_name] = StageProgressDict(
                total=row.total,
                processed=row.processed,
                avg_ms=row.avg_ms,
                started_at=row.started_at,
                last_activity=row.last_activity,
                completed_at=row.completed_at,
                extras=json.loads(row.extras_json) if row.extras_json is not None else None,
            )
        return out
