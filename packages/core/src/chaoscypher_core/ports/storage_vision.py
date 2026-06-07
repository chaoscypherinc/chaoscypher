# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Vision storage port — protocol satisfied by the SQLite adapter mixin.

Mirrors the SourceStorageProtocol shape (sync ``def`` methods;
services wrap calls in ``asyncio.to_thread()`` when invoking from
async context). Returns TypedDicts, not entities — call sites use
``data["key"]`` access (CC002).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

from chaoscypher_core.vision.states import VisionPageStatus


if TYPE_CHECKING:
    from datetime import datetime


class VisionJob(TypedDict):
    """Coordinator row for one source's per-page vision processing."""

    id: str
    source_id: str
    total_pages: int
    completed: int
    failed: int
    created_at: datetime
    updated_at: datetime


class VisionPageDescription(TypedDict):
    """One vision-page row. ``status`` is a VisionPageStatus value."""

    id: str
    source_id: str
    vision_job_id: str
    page_number: int
    region_index: int
    kind: str
    status: str
    description: str | None
    image_path: str
    finish_reason: str | None
    error_message: str | None
    attempts: int
    created_at: datetime
    updated_at: datetime


class VisionStorageProtocol(Protocol):
    """Per-page vision storage operations.

    Sync ``def`` methods (matches the rest of the storage-port surface).
    The indexing handler — the only caller in PR 2 — invokes these
    via ``await asyncio.to_thread(...)`` where appropriate.
    """

    def create_vision_job_with_pages(
        self,
        *,
        source_id: str,
        pages: Sequence[Mapping[str, Any]],
    ) -> str:
        """Insert vision_jobs row + N pending vision_page_descriptions rows in one transaction.

        Returns vision_jobs.id.

        ``pages`` items must each contain keys:
            page_number (int), kind (VisionPageKind value), image_path (str)

        The caller is expected to wrap an outer ``adapter.transaction()``
        that also flips ``sources.state`` to ``'vision_pending'`` so
        both writes are atomic together. The indexing handler is the
        only caller and follows this pattern.
        """
        ...

    def get_vision_job(self, job_id: str) -> VisionJob | None:
        """Return the vision_jobs row by id, or None."""
        ...

    def get_vision_job_by_source(self, source_id: str) -> VisionJob | None:
        """Return the most recent vision_jobs row for a source, or None.

        v1 only ever creates one job per source; this method is for
        recovery and UI.
        """
        ...

    def list_vision_page_descriptions(
        self,
        source_id: str,
        *,
        statuses: Sequence[VisionPageStatus] | None = None,
    ) -> list[VisionPageDescription]:
        """Return all rows for a source, optionally filtered by status.

        Ordered by (page_number, region_index) for deterministic merge.
        """
        ...

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
        """Atomic guarded update. Returns rows affected (1 or 0).

        SQL shape:
            UPDATE vision_page_descriptions
               SET status = :new_status,
                   description = :description,
                   finish_reason = :finish_reason,
                   error_message = :error_message,
                   attempts = attempts + 1,
                   updated_at = now()
             WHERE id = :page_id AND status = :expected_current_status

        ``rows == 0`` means stale dispatch: the row's status has already
        moved past the expected value (e.g. manual retry reset it).
        Caller MUST treat 0 as a no-op (no counter bump, no finalize
        enqueue).
        """
        ...

    def increment_vision_job_completed_and_check(
        self,
        *,
        job_id: str,
        outcome: VisionPageStatus,
    ) -> dict[str, Any]:
        """Atomically bump the appropriate counter and check terminal.

        Mirrors ``increment_job_completed_and_check`` for chunk
        extraction. Single UPDATE with arithmetic expression
        (``completed = completed + 1``) — SQLite serializes at the
        DB level. Re-reads the row after commit to compute
        ``is_terminal = (completed + failed >= total_pages)``.

        Outcome mapping:
            SUCCEEDED  → completed += 1
            TRUNCATED  → completed += 1  (we got content)
            FAILED     → failed += 1
            PENDING    → ValueError (not a terminal outcome)

        Returns ``{"completed": int, "failed": int, "total": int,
        "is_terminal": bool}``.
        """
        ...

    def reset_vision_page_for_retry(
        self,
        *,
        page_id: str,
    ) -> bool:
        """One-transaction: row → PENDING, decrement vision_jobs counter.

        Clears description / finish_reason / error_message.

        Returns True if reset happened. False when the row is already
        PENDING (no-op — idempotent) or doesn't exist.

        The counter decremented depends on the row's CURRENT status:
            SUCCEEDED, TRUNCATED → completed -= 1
            FAILED                → failed -= 1
            PENDING               → return False (already pending)
        """
        ...
