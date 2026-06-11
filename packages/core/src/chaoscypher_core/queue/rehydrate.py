# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Re-enqueue DB-persisted tasks whose Valkey state is missing.

Called at startup after DB init and before workers begin dequeueing. Closes
two durability gaps:

1. Enqueue atomicity -- crash between DB INSERT and Valkey enqueue leaves an
   orphan DB row that the reconciler never sees (because the reconciler only
   inspects rows already in Valkey running-set).
2. AOF wipe recovery -- if Valkey's AOF was corrupted and rebuilt empty,
   rows that were running/queued in the DB have no corresponding queue hash;
   they would sit in a non-terminal state indefinitely.

The rehydrator walks a registry of operation-bearing DB tables (see
``REHYDRATION_REGISTRY``). Each entry maps a SQLModel table whose rows
carry a ``queue_task_id`` column onto a queue / operation pair drawn from
the canonical ``OPERATION_QUEUE_ROUTING`` mapping. For every row in a
non-terminal status whose Valkey hash has gone missing, the rehydrator
re-enqueues a new task and updates the row to point at the fresh task ID.

Operations without a DB-row representation
------------------------------------------

Most queue operations do NOT have a dedicated DB table tracking individual
queue tasks. Their durability story leans on other mechanisms instead:

* **Source-lifecycle ops** -- ``index_document`` / ``import_analysis`` /
  ``import_commit`` / ``vision_page`` / ``vision_finalize`` /
  ``finalize_extraction``. These track state on ``SourceRow.status`` (or
  ``VisionJob`` / ``ChunkExtractionJob``) but never persist a
  ``queue_task_id`` for the in-flight task. Recovery is handled by
  ``chaoscypher_core.services.sources.recovery.SourceRecovery`` which
  classifies sources by status and re-dispatches the appropriate stage.
* **Chat ops** -- ``chat_completion`` / ``chat_background`` /
  ``tool_execution``. Transient; on crash the chat is marked ``error`` by
  ``queue.upgrade_recovery`` and the user clicks retry.
* **Drop-and-log ops** -- ``export_graph`` / ``bulk_*`` / ``reset_*`` /
  ``graph_cleanup`` / ``cleanup_orphans`` / ``build_graph_snapshot`` /
  ``rebuild_search_indexes`` / ``fetch_url`` / ``import_ccx`` /
  ``execute_workflow`` / ``execute_step`` / ``recalculate_quality_scores``
  / ``regenerate_template_embeddings`` / ``lexicon_import``. No owning
  resource to recover; an interrupted task is silently dropped and the
  user re-issues the request.

These categories are encoded in
``chaoscypher_core.queue.upgrade_recovery.OPERATION_RECOVERY_CATEGORY``.
Adding a DB-row representation for any of them would make them rehydratable
here: add a new entry to ``REHYDRATION_REGISTRY``.

Usage::

    from chaoscypher_core.queue.rehydrate import rehydrate_queue_from_db
    from chaoscypher_core.adapters.sqlite.session import get_db_session

    with get_db_session(settings.current_database) as session:
        count = await rehydrate_queue_from_db(queue_client, session)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.constants import OP_EXTRACT_CHUNK, OPERATION_QUEUE_ROUTING


if TYPE_CHECKING:
    from chaoscypher_core.queue.client import QueueClient

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Registry of operation-bearing DB tables
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RehydrationSpec:
    """One operation-bearing DB table's rehydration contract.

    The registry binds a SQLModel ``table_factory`` (lazy import to keep
    this module free of heavyweight model imports at startup) onto a queue
    operation. The rehydrator iterates the registry, queries each table
    for rows in ``non_terminal_statuses`` whose Valkey hash is gone, and
    re-enqueues them on ``queue`` / ``operation``.

    Attributes:
        operation: Canonical op-name from ``OPERATION_QUEUE_ROUTING``. The
            queue is looked up via that mapping (single source of truth;
            see CC044).
        table_factory: Zero-arg callable returning the SQLModel class. Lazy
            so the registry module can be imported without pulling the full
            ``adapters/sqlite/models.py`` graph.
        non_terminal_statuses: Statuses considered "in flight"; only rows
            in one of these are candidates for re-enqueue. Terminal states
            (``completed``, ``failed``, ``cancelled``, ``orphaned``) are
            skipped.
        build_payload: Callable mapping a fetched task row onto the
            ``(data, metadata)`` tuple passed to ``queue_client.enqueue``.
            Returning fresh metadata lets the rehydrator stamp
            ``rehydrated=True`` and ``prior_status`` on every re-enqueued
            task for observability.
        reset_row: Callable taking ``(row, new_queue_task_id)`` that
            mutates the row to reflect the fresh enqueue -- typically
            ``status = "queued"``, ``started_at = None``, and
            ``queue_task_id = new_queue_task_id``.
    """

    operation: str
    table_factory: Callable[[], type]
    non_terminal_statuses: tuple[str, ...]
    build_payload: Callable[[Any], tuple[dict[str, Any], dict[str, Any]]]
    reset_row: Callable[[Any, str], None]


def _chunk_extraction_task_factory() -> type:
    """Lazy import of ``ChunkExtractionTask`` to keep registry import light."""
    from chaoscypher_core.adapters.sqlite.models import ChunkExtractionTask

    return ChunkExtractionTask


def _build_extract_chunk_payload(task: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Construct the ``OP_EXTRACT_CHUNK`` enqueue payload from a task row.

    The chunk handler reads chunk content out of the DB at execution time
    (the queue payload carries only IDs), so ``chunk_content``
    is intentionally empty.
    """
    data: dict[str, Any] = {
        "chunk_task_id": task.id,
        "job_id": task.job_id,
        "database_name": task.database_name,
        "chunk_content": "",
        "chunk_index": task.chunk_index,
        "hierarchical_group_id": task.hierarchical_group_id,
        "small_chunk_ids": task.small_chunk_ids,
    }
    metadata: dict[str, Any] = {
        "job_id": task.job_id,
        "chunk_task_id": task.id,
        "chunk_index": task.chunk_index,
        "operation_type": OP_EXTRACT_CHUNK,
        "rehydrated": True,
        "prior_status": task.status,
    }
    return data, metadata


def _reset_extract_chunk_row(task: Any, new_queue_task_id: str) -> None:
    """Reset a ``ChunkExtractionTask`` row after a fresh enqueue."""
    task.status = "queued"
    task.started_at = None
    task.queue_task_id = new_queue_task_id


#: The full set of operation-bearing tables the rehydrator walks.
#:
#: Today this contains a single entry: ``ChunkExtractionTask`` is the only
#: table in the schema that carries a ``queue_task_id`` column tied to an
#: individual queue task. See the module docstring for why the other
#: operations have no entry here (and the recovery path they rely on
#: instead).
REHYDRATION_REGISTRY: tuple[RehydrationSpec, ...] = (
    RehydrationSpec(
        operation=OP_EXTRACT_CHUNK,
        table_factory=_chunk_extraction_task_factory,
        non_terminal_statuses=("pending", "queued", "running"),
        build_payload=_build_extract_chunk_payload,
        reset_row=_reset_extract_chunk_row,
    ),
)


# ---------------------------------------------------------------------------
# Rehydrator
# ---------------------------------------------------------------------------


async def _rehydrate_spec(
    queue_client: QueueClient,
    session: Any,
    spec: RehydrationSpec,
) -> tuple[int, int]:
    """Re-enqueue in-flight rows for a single ``RehydrationSpec``.

    Walks every row of ``spec.table_factory()`` in a non-terminal status
    (with ``cancelled_at IS NULL`` when the table has a ``cancelled_at``
    column) and re-enqueues any whose Valkey hash is missing.

    Returns:
        ``(rehydrated, scanned)`` -- number of rows actually re-enqueued
        and total number of rows that matched the non-terminal filter.
    """
    from sqlmodel import select

    table = spec.table_factory()
    queue_name = OPERATION_QUEUE_ROUTING[spec.operation]

    stmt: Any = select(table).where(
        table.status.in_(spec.non_terminal_statuses),  # type: ignore[attr-defined]
    )
    # ``cancelled_at IS NULL`` filter when the table exposes the column.
    if hasattr(table, "cancelled_at"):
        stmt = stmt.where(table.cancelled_at.is_(None))  # type: ignore[attr-defined]

    rows = list(session.exec(stmt))
    rehydrated = 0

    for row in rows:
        # Fast path: if the Valkey hash is still present, skip (no duplicate).
        existing_qtid = getattr(row, "queue_task_id", None)
        if existing_qtid is not None:
            key = f"queue:task:{existing_qtid}"
            if await queue_client.client.exists(key):
                continue

        logger.warning(
            "queue_task_rehydrating",
            operation=spec.operation,
            task_id=getattr(row, "id", None),
            prior_status=getattr(row, "status", None),
            queue_task_id=existing_qtid,
        )

        data, metadata = spec.build_payload(row)
        new_queue_task_id = await queue_client.enqueue(
            queue=queue_name,
            operation=spec.operation,
            data=data,
            metadata=metadata,
            priority=0,
        )

        spec.reset_row(row, new_queue_task_id)
        # Commit the repoint immediately, before the next row's enqueue. The
        # enqueue above already persisted a fresh Valkey task; if we deferred
        # the row mutation to a single end-of-pass commit, a crash on a later
        # row (e.g. Valkey drop) would roll back this row's queue_task_id
        # repoint while the fresh task lived on in Valkey. The next startup
        # would then see this row's stale (missing) hash and re-enqueue it
        # again -> duplicate task. Per-row commit bounds the duplicate window
        # to at most the single row in flight when the crash lands.
        session.maybe_commit()
        rehydrated += 1

    return rehydrated, len(rows)


async def rehydrate_queue_from_db(queue_client: QueueClient, session: Any) -> int:
    """Re-enqueue in-flight DB rows whose Valkey hash is missing.

    Iterates ``REHYDRATION_REGISTRY`` and, for each operation-bearing
    table, queries rows in a non-terminal status (plus
    ``cancelled_at IS NULL`` when the table has that column) and checks
    whether their Valkey hash still exists. If the hash is gone (AOF wipe
    or crash-between-insert-and-enqueue), the task is re-enqueued on its
    canonical queue, ``queue_task_id`` is repointed at the new task, and
    the row is reset to ``status="queued"`` with ``started_at=None`` so a
    worker picks it up fresh.

    Rows whose ``queue_task_id`` is ``None`` were never enqueued at all --
    they are unconditionally re-enqueued.

    Operations without a DB-row representation (the majority of the
    routing table -- see the module docstring) are NOT covered here.
    They rely on ``SourceRecovery`` (source-lifecycle ops),
    ``queue.upgrade_recovery`` (chat ops), or silent drop (idempotent
    system ops).

    Args:
        queue_client: Connected QueueClient instance (must have ``.client``
            set).
        session: Synchronous SafeSession for the target database.

    Returns:
        Total number of rows that were rehydrated (re-enqueued) across all
        registered specs.
    """
    total_rehydrated = 0
    total_scanned = 0

    for spec in REHYDRATION_REGISTRY:
        try:
            spec_rehydrated, spec_scanned = await _rehydrate_spec(queue_client, session, spec)
            total_rehydrated += spec_rehydrated
            total_scanned += spec_scanned

            logger.info(
                "queue_rehydration_spec_complete",
                operation=spec.operation,
                rehydrated=spec_rehydrated,
                scanned=spec_scanned,
            )
        except Exception:
            # Best-effort: a failure on one spec must not block the rest.
            # Worker startup logs the swallowed exception so an operator can
            # see what broke without losing rehydration of healthy tables.
            logger.exception(
                "queue_rehydration_spec_failed",
                operation=spec.operation,
            )

    # No end-of-pass commit: ``_rehydrate_spec`` commits each row's repoint
    # the moment its fresh task is enqueued, so committed work survives a
    # mid-loop crash without re-enqueuing duplicates on the next startup.

    logger.info(
        "queue_rehydration_complete",
        rehydrated=total_rehydrated,
        scanned=total_scanned,
        specs=len(REHYDRATION_REGISTRY),
    )
    return total_rehydrated
