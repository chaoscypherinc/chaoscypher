# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""OP_VISION_FINALIZE handler.

After the last per-page vision task completes, this handler:

1. Validates the job/source exist and the source is still ``vision_pending``
   (idempotency short-circuit — re-runs read from durable state and bail
   when state has already advanced).
2. Re-runs the loader to get the pre-vision documents list (deterministic
   for already-staged files; the loader is the single source of truth for
   document structure + page metadata).
3. Reads every ``vision_page_descriptions`` row for the source.
4. Splices ``SUCCEEDED`` + ``TRUNCATED`` descriptions back into the
   document content (port of the legacy phase-3 merge in
   ``_apply_vision_processing``: for PDF pages, append a
   ``[Visual Content]`` block to the page's text and rejoin into the
   document content; for standalone images, replace the document content
   with the description).
5. Transitions the source from ``VISION_PENDING`` to ``INDEXING`` via an
   atomic compare-and-swap.
6. Enqueues the next indexing step — a fresh ``OP_INDEX_DOCUMENT`` task
   with ``resume_after_vision=True`` so the indexing handler can skip
   the vision phase and continue with normalize → chunk → embed.

Idempotent: re-runs read from durable state and converge regardless of
where a prior attempt crashed:

* ``status == VISION_PENDING`` → fall through to CAS + enqueue.
* ``status == INDEXING`` AND a ``vision_job`` exists → post-CAS-pre-enqueue
  crash window. Re-emit the ``OP_INDEX_DOCUMENT`` resume task (debounced
  against the queue so a concurrent recovery scan does not double-enqueue).
* Any other status → already fully advanced past indexing (or never
  entered the vision phase). Skip cleanly.

The splice helpers (``_splice_descriptions_into_documents``) are exported
so the indexing handler's resume branch can reuse the exact
same merge logic against an in-memory re-load, keeping the splice as the
single source of truth.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.constants import (
    OP_INDEX_DOCUMENT,
    QUEUE_OPERATIONS,
)
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.queue import queue_client
from chaoscypher_core.vision.states import VisionPageStatus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.app_config import Settings


logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers (also re-used by the indexing handler's resume branch, Task 12).
# ---------------------------------------------------------------------------


def _reload_documents(
    adapter: SqliteAdapter,
    source_id: str,
    database_name: str,
    settings: Settings,
) -> list[dict[str, Any]]:
    """Re-run the loader phase for a source to recover pre-vision documents.

    The loader registry is deterministic for already-staged files: opening
    the same file with the same settings yields the same documents +
    per-page metadata. The finalizer relies on this so the descriptions
    persisted under ``vision_page_descriptions`` (keyed by
    ``page_number``) line up exactly with the loader's page enumeration on
    the second pass.

    PDFs typically re-load in well under a second; smaller text-only
    formats are negligible. The re-run is the simplest path that keeps
    the document state durable across crashes without introducing a new
    intermediate persistence layer.

    Args:
        adapter: Storage adapter (used to read the source row).
        source_id: Source whose loader output to recover.
        database_name: Database that owns the source.
        settings: Application settings (carries the engine config the
            loader registry resolves from).

    Returns:
        The loader's pre-vision document list. Empty list if the source
        row or filepath is missing — caller decides how to handle.
    """
    from chaoscypher_core.app_config.engine_factory import build_engine_settings
    from chaoscypher_core.services.sources.loaders import get_loader_registry

    src = adapter.get_source(source_id, database_name)
    if src is None:
        logger.warning(
            "vision_finalize_reload_source_missing",
            source_id=source_id,
            database_name=database_name,
        )
        return []
    filepath = src.get("filepath")
    if not filepath:
        logger.warning(
            "vision_finalize_reload_filepath_missing",
            source_id=source_id,
            database_name=database_name,
        )
        return []

    engine_settings = build_engine_settings(settings)
    loader_registry = get_loader_registry(engine_settings)
    return loader_registry.load_document(filepath)


def _splice_descriptions_into_documents(
    documents: list[dict[str, Any]],
    page_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    r"""Splice vision descriptions into documents by page number.

    Mirrors the Phase-3 merge in the legacy ``_apply_vision_processing``
    (indexing_handler.py:1599-1660):

    * For PDF pages with a vision description, append
      ``\\n\\n[Visual Content]\\n{description}\\n[/Visual Content]`` to
      the page's text (read from ``metadata["_page_texts"]``) and rejoin
      via ``"\\n\\n"`` into the document's ``content``.
    * For standalone images, replace the document's ``content`` entirely
      with the description and set ``metadata["extraction_method"] =
      "vision"``.

    Skipped rows (status != ``SUCCEEDED`` and != ``TRUNCATED``, or
    description is ``None``) contribute nothing — the document keeps its
    loader text. This is consistent with the legacy behaviour: failed
    pages stay un-augmented.

    The function MUTATES ``documents`` in-place (mirrors the legacy
    behaviour where the merge was an in-place mutation) and also returns
    the list for fluent chaining.

    Args:
        documents: Loader output (pre-vision). Each document carries
            ``content`` + ``metadata`` keys.
        page_rows: All ``vision_page_descriptions`` rows for this source,
            ordered by ``(page_number, region_index)``.

    Returns:
        The same ``documents`` list with descriptions spliced in.
    """
    from chaoscypher_core.vision.states import VisionPageKind

    # Build page-number → first matching document index lookup so we can
    # locate the document a PDF page belongs to. Loader output for plain
    # PDFs is a single document whose metadata["pages"] enumerates page
    # info — every PDF page maps to doc_idx 0. Archive loaders may emit
    # multiple documents; we still match by page_number against each
    # document's _page_texts length.
    successful_statuses = {VisionPageStatus.SUCCEEDED.value, VisionPageStatus.TRUNCATED.value}

    for row in page_rows:
        if row["status"] not in successful_statuses:
            continue
        description = row.get("description")
        if not description:
            # Belt-and-braces: SUCCEEDED + empty description is degenerate
            # but harmless. Skip rather than write an empty marker.
            continue

        kind = row["kind"]
        page_number = row["page_number"]

        if kind == VisionPageKind.STANDALONE_IMAGE.value:
            # Standalone image: the loader emitted one document per image
            # with extraction_method="vision_pending". Find it and replace
            # its content. Matching by page_number=1 + extraction_method
            # mirrors the legacy collector logic in _apply_vision_processing.
            for doc in documents:
                metadata = doc.get("metadata", {})
                if not isinstance(metadata, dict):
                    continue
                if metadata.get("extraction_method") == "vision_pending":
                    doc["content"] = description
                    metadata["extraction_method"] = "vision"
                    break
        else:
            # PDF page: find the document whose _page_texts covers this
            # page_number and splice in-place.
            for doc in documents:
                metadata = doc.get("metadata", {})
                if not isinstance(metadata, dict):
                    continue
                page_texts = metadata.get("_page_texts") or []
                if not page_texts or page_number > len(page_texts):
                    continue
                page_texts[page_number - 1] = (
                    f"{page_texts[page_number - 1]}\n\n[Visual Content]\n"
                    f"{description}\n[/Visual Content]"
                )
                doc["content"] = "\n\n".join(page_texts)
                # Carry the image_path forward into per-page metadata so
                # downstream UI can render thumbnails. Mirrors the legacy
                # post-merge loop.
                image_path = row.get("image_path")
                if image_path:
                    for p in metadata.get("pages", []) or []:
                        if p.get("page_number") == page_number:
                            p["image_path"] = image_path
                            break
                break

    return documents


def _file_info_from_source(src: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the indexing ``file_info`` payload from a source row.

    Mirrors the original upload-time metadata the indexing handler
    consumes — the source row is the single source of truth for the
    second-pass resume since the in-memory ``file_info`` from the first
    pass is gone after a crash.
    """
    return {
        "filepath": src.get("filepath"),
        "filename": src.get("filename"),
        "file_type": src.get("file_type"),
        "enable_vision": src.get("enable_vision"),
        "enable_normalization": src.get("enable_normalization"),
        "extraction_depth": src.get("extraction_depth"),
    }


async def _enqueue_resume_indexing(
    *,
    source_id: str,
    database_name: str,
    file_info: dict[str, Any],
) -> None:
    """Enqueue an OP_INDEX_DOCUMENT to resume after vision.

    The resume task carries ``resume_after_vision=True`` so the indexing
    handler can detect the second-pass entry, skip the
    vision phase entirely (page rows are already terminal), re-load the
    documents + descriptions, and continue with chunking + embedding.

    Mirrors the ``queue_import_indexing`` shape but adds the resume flag
    on the payload so the handler has an unambiguous signal.
    """
    await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation=OP_INDEX_DOCUMENT,
        data={
            "file_id": source_id,
            "file_info": file_info,
            "resume_after_vision": True,
        },
        metadata={
            "source_id": source_id,
            "database_name": database_name,
            "operation_type": OP_INDEX_DOCUMENT,
            "resume_after_vision": True,
        },
    )
    logger.info(
        "vision_finalize_resume_enqueued",
        source_id=source_id,
        database_name=database_name,
    )


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def handle_vision_finalize(  # noqa: PLR0911 - idempotency state machine; one return per terminal arm
    data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
    *,
    adapter: SqliteAdapter,
    settings: Settings,
) -> dict[str, Any]:
    """Handle one OP_VISION_FINALIZE queue task.

    Idempotency contract:

    * Job missing → ``{"status": "skipped_missing"}``.
    * Source missing → ``{"status": "skipped_missing"}``.
    * Source status == ``INDEXING`` AND a ``vision_job`` exists AND no
      ``OP_INDEX_DOCUMENT`` is queued → re-emit the resume task →
      ``{"status": "re_emitted_resume"}``. Covers the post-CAS-pre-enqueue
      crash window the recovery scanner routes back here.
    * Source status == ``INDEXING`` AND a ``vision_job`` exists AND an
      ``OP_INDEX_DOCUMENT`` is already queued → debounce →
      ``{"status": "skipped_resume_already_queued"}``.
    * Source fully advanced past ``indexing`` (or never entered vision)
      → ``{"status": "skipped_already_advanced"}`` (no state mutation,
      no enqueue).
    * Atomic CAS from ``vision_pending`` → ``indexing`` failed (lost
      the race to a concurrent finalize) → ``{"status":
      "skipped_already_advanced"}``.
    * Happy path → ``{"status": "success", ...}``.

    The handler does NOT raise on best-effort steps (loader re-run
    failures, splice degenerate cases). Hard errors (DB write failures,
    missing critical state) propagate.

    Args:
        data: Task payload — ``{"source_id", "job_id", "database_name"}``.
        metadata: Queue metadata (unused by handler body).
        task_id: Queue task ID (unused by handler body).
        adapter: Storage adapter (keyword-only, injected by service).
        settings: Application settings (keyword-only, injected by service).

    Returns:
        Result dict with ``"status"`` and (on success) merge statistics.
    """
    source_id = data["source_id"]
    job_id = data["job_id"]
    database_name = data["database_name"]

    # 1. Validate job exists.
    job = await asyncio.to_thread(adapter.get_vision_job, job_id)
    if job is None:
        logger.warning(
            "vision_finalize_job_missing",
            source_id=source_id,
            job_id=job_id,
        )
        return {"status": "skipped_missing"}

    # 2. Validate source exists.
    src = await asyncio.to_thread(adapter.get_source, source_id, database_name)
    if src is None:
        logger.warning(
            "vision_finalize_source_missing",
            source_id=source_id,
            database_name=database_name,
        )
        return {"status": "skipped_missing"}

    # 3. Idempotency.
    #
    # Three sub-cases, indexed off the durable source status:
    #
    # a. VISION_PENDING → first finalize attempt (or a clean retry before
    #    any prior attempt CAS'd). Fall through to the CAS-and-enqueue
    #    happy path below.
    #
    # b. INDEXING + a vision_job exists for the source → a prior finalize
    #    succeeded at the VISION_PENDING → INDEXING CAS (step 7 below)
    #    but crashed before enqueuing the resume task (step 8). The
    #    recovery scanner's vision_job-aware indexing dispatch routes
    #    this case back here expecting the finalizer to converge it;
    #    without converging we get a permanent stall (recovery → finalize
    #    → skip → repeat). Re-emit the OP_INDEX_DOCUMENT resume task with
    #    the same file_info the success path uses, debounced against the
    #    queue so concurrent recovery scans do not enqueue duplicates.
    #
    # c. Any other status (indexed, error, pending, …) → either fully
    #    advanced past indexing or never entered vision. Skip cleanly.
    current_status = src.get("status")
    if current_status == SourceStatus.INDEXING.value and job is not None:
        # Sub-case (b): post-CAS-pre-enqueue crash window. Debounce against
        # an in-flight OP_INDEX_DOCUMENT first — if recovery (or a prior
        # finalize retry) already re-emitted, do not double-enqueue.
        resume_in_flight = await queue_client.task_exists_for_source(
            source_id=source_id,
            database_name=database_name,
            operations=[OP_INDEX_DOCUMENT],
        )
        if resume_in_flight:
            logger.info(
                "vision_finalize_resume_already_queued",
                source_id=source_id,
                job_id=job_id,
                current_status=current_status,
            )
            return {"status": "skipped_resume_already_queued"}

        file_info = _file_info_from_source(src)
        await _enqueue_resume_indexing(
            source_id=source_id,
            database_name=database_name,
            file_info=file_info,
        )
        logger.info(
            "vision_finalize_re_emitted_resume",
            source_id=source_id,
            job_id=job_id,
            current_status=current_status,
            job_completed=job.get("completed"),
            job_failed=job.get("failed"),
            job_total=job.get("total_pages"),
        )
        return {"status": "re_emitted_resume"}

    if current_status != SourceStatus.VISION_PENDING.value:
        # Sub-case (c): fully advanced past indexing, or never entered vision.
        logger.info(
            "vision_finalize_already_advanced",
            source_id=source_id,
            current_status=current_status,
            job_id=job_id,
        )
        return {"status": "skipped_already_advanced"}

    # 4. Re-load documents (deterministic loader call).
    documents = await asyncio.to_thread(
        _reload_documents, adapter, source_id, database_name, settings
    )

    # 5. Read all page descriptions.
    page_rows = await asyncio.to_thread(adapter.list_vision_page_descriptions, source_id)

    # 6. Splice descriptions into documents (in-memory; the indexing
    #    resume path will re-run the same splice deterministically).
    _splice_descriptions_into_documents(documents, page_rows)

    succeeded_count = sum(1 for r in page_rows if r["status"] == VisionPageStatus.SUCCEEDED.value)
    truncated_count = sum(1 for r in page_rows if r["status"] == VisionPageStatus.TRUNCATED.value)
    failed_count = sum(1 for r in page_rows if r["status"] == VisionPageStatus.FAILED.value)

    # 7. Atomic CAS transition VISION_PENDING → INDEXING. A concurrent
    #    finalize would race here; the CAS guarantees exactly one
    #    finalizer enqueues the resume task.
    transitioned = await asyncio.to_thread(
        adapter.transition_source_status,
        source_id,
        from_status=SourceStatus.VISION_PENDING.value,
        to_status=SourceStatus.INDEXING.value,
        database_name=database_name,
    )
    if not transitioned:
        logger.info(
            "vision_finalize_transition_lost_race",
            source_id=source_id,
            job_id=job_id,
        )
        return {"status": "skipped_already_advanced"}

    # 8. Enqueue the indexing-resume task. file_info comes from the
    #    source row (the original upload metadata is the source of
    #    truth for the resume — same fields the original enqueue
    #    populated).
    file_info = _file_info_from_source(src)
    await _enqueue_resume_indexing(
        source_id=source_id,
        database_name=database_name,
        file_info=file_info,
    )

    logger.info(
        "vision_finalize_complete",
        source_id=source_id,
        job_id=job_id,
        total_pages=len(page_rows),
        succeeded=succeeded_count,
        truncated=truncated_count,
        failed=failed_count,
        document_count=len(documents),
    )
    return {
        "status": "success",
        "total_pages": len(page_rows),
        "succeeded": succeeded_count,
        "truncated": truncated_count,
        "failed": failed_count,
        "document_count": len(documents),
    }


__all__ = [
    "_enqueue_resume_indexing",
    "_file_info_from_source",
    "_reload_documents",
    "_splice_descriptions_into_documents",
    "handle_vision_finalize",
]
