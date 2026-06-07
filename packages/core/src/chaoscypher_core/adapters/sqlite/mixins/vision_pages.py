# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Vision Pages Mixin for SqliteAdapter.

Per-page vision processing operations. Mirrors the shape of
SourceExtractionJobsMixin (sync methods, _maybe_commit pattern,
expire_all before re-read for atomic-counter accuracy).

Implements VisionStorageProtocol.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import update as sqla_update
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import VisionJob, VisionPageDescription
from chaoscypher_core.utils.id import generate_id
from chaoscypher_core.vision.states import VisionPageStatus


logger = structlog.get_logger(__name__)


class VisionPagesMixin(SqliteMixinBase):
    """Mixin providing per-page vision storage operations.

    Contributes to the unified storage adapter surface.
    Implements VisionStorageProtocol.
    """

    def create_vision_job_with_pages(
        self,
        *,
        source_id: str,
        pages: Sequence[Mapping[str, Any]],
    ) -> str:
        """Insert vision_jobs row + N pending vision_page_descriptions rows in one transaction.

        Args:
            source_id: ID of the source whose pages are being described.
            pages: Sequence of page dicts with keys ``page_number``, ``kind``,
                and ``image_path``.

        Returns:
            The newly created ``vision_jobs.id`` (prefixed ``vjob_``).
        """
        self._ensure_connected()

        job_id = generate_id("vjob")
        now = datetime.now(UTC)

        job = VisionJob(
            id=job_id,
            source_id=source_id,
            total_pages=len(pages),
            completed=0,
            failed=0,
            created_at=now,
            updated_at=now,
        )
        self.session.add(job)

        for page in pages:
            row = VisionPageDescription(
                id=generate_id("vpd"),
                source_id=source_id,
                vision_job_id=job_id,
                page_number=int(page["page_number"]),
                region_index=0,
                kind=str(page["kind"]),  # StrEnum stringifies to its value
                status=VisionPageStatus.PENDING.value,
                description=None,
                image_path=str(page["image_path"]),
                finish_reason=None,
                error_message=None,
                attempts=0,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)

        self._maybe_commit()
        return job_id

    # --- stubs for tasks 6-10 (each lands its own implementation) ---

    def get_vision_job(self, job_id: str) -> dict[str, Any] | None:
        """Return the vision_jobs row by id, or None."""
        self._ensure_connected()
        job = self.session.scalars(select(VisionJob).where(VisionJob.id == job_id)).first()
        return self._vision_job_to_dict(job) if job is not None else None

    def get_vision_job_by_source(self, source_id: str) -> dict[str, Any] | None:
        """Return the most recent vision_jobs row for a source, or None."""
        self._ensure_connected()
        job = self.session.scalars(
            select(VisionJob)
            .where(VisionJob.source_id == source_id)
            .order_by(VisionJob.created_at.desc())  # type: ignore[attr-defined]
        ).first()
        return self._vision_job_to_dict(job) if job is not None else None

    @staticmethod
    def _vision_job_to_dict(job: VisionJob) -> dict[str, Any]:
        """Convert SQLModel entity to the VisionJob TypedDict shape."""
        return {
            "id": job.id,
            "source_id": job.source_id,
            "total_pages": job.total_pages,
            "completed": job.completed,
            "failed": job.failed,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

    def list_vision_page_descriptions(
        self,
        source_id: str,
        *,
        statuses: Sequence[VisionPageStatus] | None = None,
    ) -> list[dict[str, Any]]:
        """Return all rows for a source, optionally filtered by status.

        Ordered by (page_number, region_index).
        """
        self._ensure_connected()

        stmt = (
            select(VisionPageDescription)
            .where(VisionPageDescription.source_id == source_id)
            .order_by(
                VisionPageDescription.page_number,  # type: ignore[arg-type]
                VisionPageDescription.region_index,  # type: ignore[arg-type]
            )
        )
        if statuses:
            status_values = [s.value for s in statuses]
            stmt = stmt.where(VisionPageDescription.status.in_(status_values))  # type: ignore[attr-defined]

        rows = self.session.scalars(stmt).all()
        return [self._vision_page_to_dict(r) for r in rows]

    @staticmethod
    def _vision_page_to_dict(row: VisionPageDescription) -> dict[str, Any]:
        """Convert SQLModel entity to the VisionPageDescription TypedDict shape."""
        return {
            "id": row.id,
            "source_id": row.source_id,
            "vision_job_id": row.vision_job_id,
            "page_number": row.page_number,
            "region_index": row.region_index,
            "kind": row.kind,
            "status": row.status,
            "description": row.description,
            "image_path": row.image_path,
            "finish_reason": row.finish_reason,
            "error_message": row.error_message,
            "attempts": row.attempts,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def update_vision_page_description(
        self,
        *,
        page_id: str,
        new_status: VisionPageStatus,
        description: str | None,
        finish_reason: str | None,
        error_message: str | None,
        expected_current_status: VisionPageStatus = VisionPageStatus.PENDING,
    ) -> int:
        """Atomic guarded update. Returns rows affected.

        rows=0 means stale dispatch: the row's status has already moved
        past expected_current_status (e.g. manual retry reset it).
        Caller MUST treat 0 as a no-op (no counter bump, no finalize
        enqueue).
        """
        self._ensure_connected()

        stmt = (
            sqla_update(VisionPageDescription)
            .where(VisionPageDescription.id == page_id)
            .where(VisionPageDescription.status == expected_current_status.value)
            .values(
                status=new_status.value,
                description=description,
                finish_reason=finish_reason,
                error_message=error_message,
                attempts=VisionPageDescription.attempts + 1,
                updated_at=datetime.now(UTC),
            )
        )
        result = self.session.execute(stmt)
        self._maybe_commit()
        return int(result.rowcount or 0)

    def increment_vision_job_completed_and_check(
        self,
        *,
        job_id: str,
        outcome: VisionPageStatus,
    ) -> dict[str, Any]:
        """Atomically bump the appropriate counter and check terminal.

        Mirror of increment_job_completed_and_check for extraction.

        The bump and the read-back are fused into a SINGLE serialized
        ``UPDATE ... RETURNING`` statement so each caller observes ITS
        OWN post-increment counters. This is the primitive the
        resumability story relies on: if two workers both successfully
        process the last page, exactly one of them — the one whose
        increment made ``completed + failed`` reach ``total_pages`` —
        sees the terminal counts in its returned row, so exactly one
        finalization is enqueued.

        The previous "commit, ``expire_all``, then re-read" approach had
        a real double-finalization race: a slower caller's re-read could
        observe a faster caller's already-committed later increment, so
        BOTH read the final count and BOTH reported ``is_terminal=True``.
        """
        self._ensure_connected()

        terminal_outcomes = {
            VisionPageStatus.SUCCEEDED,
            VisionPageStatus.TRUNCATED,
            VisionPageStatus.FAILED,
        }
        if outcome not in terminal_outcomes:
            msg = (
                f"outcome must be a terminal outcome "
                f"(SUCCEEDED, TRUNCATED, FAILED); got {outcome!r}"
            )
            raise ValueError(msg)

        now = datetime.now(UTC)
        if outcome == VisionPageStatus.FAILED:
            values = {"failed": VisionJob.failed + 1, "updated_at": now}
        else:  # SUCCEEDED or TRUNCATED
            values = {"completed": VisionJob.completed + 1, "updated_at": now}

        # Fuse the increment and read-back into one serialized statement.
        # RETURNING hands back THIS caller's post-increment counters, so
        # the terminal decision is made on values that can never include
        # a concurrent caller's later increment.
        stmt = (
            sqla_update(VisionJob)
            .where(VisionJob.id == job_id)
            .values(**values)
            .returning(VisionJob.completed, VisionJob.failed, VisionJob.total_pages)
        )
        row = self.session.execute(stmt).first()
        self._maybe_commit()

        if row is None:
            return {
                "completed": 0,
                "failed": 0,
                "total": 0,
                "is_terminal": False,
            }

        completed = row.completed or 0
        failed = row.failed or 0
        total = row.total_pages or 0
        return {
            "completed": completed,
            "failed": failed,
            "total": total,
            "is_terminal": completed + failed >= total,
        }

    def reset_vision_page_for_retry(
        self,
        *,
        page_id: str,
    ) -> bool:
        """One-transaction: row → PENDING, decrement vision_jobs counter.

        Decrement is based on current status; clears description /
        finish_reason / error_message. Returns True if reset happened.
        """
        self._ensure_connected()

        row = self.session.scalars(
            select(VisionPageDescription).where(VisionPageDescription.id == page_id)
        ).first()
        if row is None:
            return False

        current = row.status
        if current == VisionPageStatus.PENDING.value:
            return False  # idempotent no-op

        if current in (
            VisionPageStatus.SUCCEEDED.value,
            VisionPageStatus.TRUNCATED.value,
        ):
            counter_stmt = (
                sqla_update(VisionJob)
                .where(VisionJob.id == row.vision_job_id)
                .values(completed=VisionJob.completed - 1, updated_at=datetime.now(UTC))
            )
        elif current == VisionPageStatus.FAILED.value:
            counter_stmt = (
                sqla_update(VisionJob)
                .where(VisionJob.id == row.vision_job_id)
                .values(failed=VisionJob.failed - 1, updated_at=datetime.now(UTC))
            )
        else:
            counter_stmt = None

        row_stmt = (
            sqla_update(VisionPageDescription)
            .where(VisionPageDescription.id == page_id)
            .values(
                status=VisionPageStatus.PENDING.value,
                description=None,
                finish_reason=None,
                error_message=None,
                updated_at=datetime.now(UTC),
            )
        )

        if counter_stmt is not None:
            self.session.execute(counter_stmt)
        self.session.execute(row_stmt)
        self._maybe_commit()
        return True
