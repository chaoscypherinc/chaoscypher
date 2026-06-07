# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Service for the vision-pages retry + listing endpoints.

Orchestrates three operations:

- ``retry_page(source_id, page_number, region_index)`` — single-page
  retry. Resets the row to PENDING (one transaction, via
  ``reset_vision_page_for_retry``), then enqueues ``OP_VISION_PAGE``.
- ``retry_failed(source_id)`` — batch retry. Lists all FAILED pages,
  resets each, enqueues one ``OP_VISION_PAGE`` per reset. TRUNCATED
  pages are skipped (v1 keeps partial content; the campaign's v2
  region-split path is a separate spec).
- ``list_pages(source_id)`` — read-only listing for the frontend
  per-page panel. Returns the ``vision_job`` summary + every page
  row, regardless of source state (works post-finalize so the
  panel can show history).

Enforces the v1 retry scope: only pre-finalize retry is supported.
If the source has advanced past vision_pending, retries are
refused with a ConflictError. ``list_pages`` is read-only and
has no such gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.constants import OP_VISION_PAGE, QUEUE_LLM
from chaoscypher_core.exceptions import ConflictError, NotFoundError
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.vision.states import VisionPageStatus


if TYPE_CHECKING:
    from chaoscypher_cortex.features.sources.vision_pages_repository import (
        VisionPagesRepository,
    )


logger = structlog.get_logger(__name__)


class VisionPagesService:
    """Orchestrates vision-page retry against the queue."""

    def __init__(
        self,
        repository: VisionPagesRepository,
        source_storage: Any,
        queue_client: Any,
        database_name: str,
    ) -> None:
        """Initialize the service.

        Args:
            repository: VSA repository over the vision storage port.
            source_storage: Source storage adapter (provides ``get_source``).
            queue_client: Queue client used to enqueue ``OP_VISION_PAGE``.
            database_name: Active database name (passed to ``get_source``).

        """
        self._repository = repository
        self._source_storage = source_storage
        self._queue_client = queue_client
        self._database_name = database_name

    def _require_pre_finalize_source(self, source_id: str) -> None:
        """Fetch source and assert it's still in vision_pending.

        Raises:
            NotFoundError: source does not exist.
            ConflictError: source has advanced past vision_pending.

        """
        source = self._source_storage.get_source(source_id, self._database_name)
        if source is None:
            raise NotFoundError("source", source_id)
        if source["status"] != SourceStatus.VISION_PENDING.value:
            msg = (
                f"source {source_id!r} is in state {source['status']!r}; "
                f"retry requires state vision_pending (post-finalize retry "
                f"is out of scope in v1)"
            )
            raise ConflictError(msg)

    async def retry_page(
        self,
        *,
        source_id: str,
        page_number: int,
        region_index: int,
    ) -> dict[str, Any]:
        """Reset one page to PENDING and re-enqueue OP_VISION_PAGE.

        Returns ``{"source_id", "page_number", "region_index", "page_id",
        "status", "reset"}`` — ``reset`` is False if the row was
        already PENDING (no-op).

        Raises:
            NotFoundError: source or page does not exist.
            ConflictError: source has advanced past vision_pending.

        """
        self._require_pre_finalize_source(source_id)

        job = self._repository.get_job_by_source(source_id)
        if job is None:
            raise NotFoundError("vision_job", f"source={source_id}")

        pages = self._repository.list_pages(source_id)
        page = next(
            (
                p
                for p in pages
                if p["page_number"] == page_number and p["region_index"] == region_index
            ),
            None,
        )
        if page is None:
            raise NotFoundError(
                "page",
                f"source={source_id} page_number={page_number} region_index={region_index}",
            )

        reset = self._repository.reset_for_retry(page["id"])
        if reset:
            await self._queue_client.enqueue(
                queue=QUEUE_LLM,
                operation=OP_VISION_PAGE,
                data={
                    "page_id": page["id"],
                    "job_id": job["id"],
                    "source_id": source_id,
                },
                metadata={
                    "source_id": source_id,
                    "page_id": page["id"],
                    "page_number": page_number,
                    "region_index": region_index,
                    "database_name": self._database_name,
                    "operation_type": OP_VISION_PAGE,
                },
            )
            logger.info(
                "vision_page_retry_enqueued",
                source_id=source_id,
                page_id=page["id"],
                page_number=page_number,
                region_index=region_index,
            )
        else:
            logger.info(
                "vision_page_retry_noop_already_pending",
                source_id=source_id,
                page_id=page["id"],
                page_number=page_number,
                region_index=region_index,
            )

        return {
            "source_id": source_id,
            "page_number": page_number,
            "region_index": region_index,
            "page_id": page["id"],
            "status": VisionPageStatus.PENDING.value if reset else page["status"],
            "reset": reset,
        }

    async def retry_failed(self, *, source_id: str) -> dict[str, Any]:
        """Reset all FAILED pages for the source and re-enqueue each.

        Returns ``{"source_id", "retried_count", "skipped_count", "page_ids"}``.
        """
        self._require_pre_finalize_source(source_id)

        job = self._repository.get_job_by_source(source_id)
        if job is None:
            raise NotFoundError("vision_job", f"source={source_id}")

        all_pages = self._repository.list_pages(source_id)
        failed_pages = [p for p in all_pages if p["status"] == VisionPageStatus.FAILED.value]
        skipped = len(all_pages) - len(failed_pages)

        retried_ids: list[str] = []
        for page in failed_pages:
            reset = self._repository.reset_for_retry(page["id"])
            if not reset:
                # Race: another caller already reset this row. Count as
                # skipped, do not enqueue.
                skipped += 1
                continue
            await self._queue_client.enqueue(
                queue=QUEUE_LLM,
                operation=OP_VISION_PAGE,
                data={
                    "page_id": page["id"],
                    "job_id": job["id"],
                    "source_id": source_id,
                },
                metadata={
                    "source_id": source_id,
                    "page_id": page["id"],
                    "page_number": page["page_number"],
                    "region_index": page["region_index"],
                    "database_name": self._database_name,
                    "operation_type": OP_VISION_PAGE,
                },
            )
            retried_ids.append(page["id"])

        logger.info(
            "vision_pages_retry_failed_complete",
            source_id=source_id,
            retried_count=len(retried_ids),
            skipped_count=skipped,
        )

        return {
            "source_id": source_id,
            "retried_count": len(retried_ids),
            "skipped_count": skipped,
            "page_ids": retried_ids,
        }

    async def list_pages(self, *, source_id: str) -> dict[str, Any]:
        """Return the vision_job summary + every page row for the source.

        Read-only; works regardless of source state (no vision_pending
        gate — the UI panel may want to show post-finalize history).

        Storage shapes differ from the API DTOs in two places, so the
        service adapts them here:

        - ``VisionJob`` (storage) → ``VisionJobSummary`` (DTO): the
          DTO requires ``is_terminal`` (computed from
          ``(completed + failed) >= total_pages``) and omits the
          storage-only ``source_id`` field.
        - ``VisionPageDescription`` (storage) → ``VisionPageResponse``
          (DTO): the storage row uses ``vision_job_id``; the DTO uses
          ``job_id``. The storage-only ``attempts`` field is dropped.

        Returns ``{"source_id", "job", "pages"}`` where ``job`` is
        ``None`` if no vision_job exists for the source (text-only
        source or pre-loader-phase).

        Raises:
            NotFoundError: source not found.

        """
        source = self._source_storage.get_source(source_id, self._database_name)
        if source is None:
            raise NotFoundError("source", source_id)

        job_row = self._repository.get_job_by_source(source_id)
        job: dict[str, Any] | None
        if job_row is None:
            job = None
        else:
            completed = job_row["completed"]
            failed = job_row["failed"]
            total = job_row["total_pages"]
            job = {
                "id": job_row["id"],
                "total_pages": total,
                "completed": completed,
                "failed": failed,
                "is_terminal": (completed + failed) >= total,
                "created_at": job_row["created_at"],
                "updated_at": job_row["updated_at"],
            }

        page_rows = self._repository.list_pages(source_id)
        pages: list[dict[str, Any]] = [
            {
                "id": p["id"],
                "source_id": p["source_id"],
                # Storage TypedDict uses ``vision_job_id``; the public
                # DTO uses ``job_id``. Rename here so the DTO's
                # ``extra="forbid"`` validator accepts the dict.
                "job_id": p["vision_job_id"],
                "page_number": p["page_number"],
                "region_index": p["region_index"],
                "kind": p["kind"],
                "status": p["status"],
                "image_path": p["image_path"],
                "description": p["description"],
                "finish_reason": p["finish_reason"],
                "error_message": p["error_message"],
                "created_at": p["created_at"],
                "updated_at": p["updated_at"],
            }
            for p in page_rows
        ]

        return {
            "source_id": source_id,
            "job": job,
            "pages": pages,
        }


__all__ = ["VisionPagesService"]
