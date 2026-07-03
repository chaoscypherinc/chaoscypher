# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Upgrade-time recovery for queue tasks with unsupported payload_version.

When a worker dequeues a task whose ``payload_version`` is not in
``SUPPORTED_PAYLOAD_VERSIONS`` (see ``queue/client.py``), the version
gate refuses to dispatch the handler — the task's ``data`` shape might
have changed between versions and silently running the new handler on
old data would produce subtle wrong output. The simple route is to mark
the queue task ``failed/permanent`` and forget it, but that leaves the
**owning resource** (source row, chat) stuck in its in-flight state
(``extracting``, ``processing``, …) with no signal to the user.

This module bridges that gap: when the version gate fires, we look up
the owning resource via the task's ``data`` payload and transition it
to a user-visible "interrupted by upgrade — retry to resume" state.
The frontend's existing retry UX takes over from there.

The contract for adding a new ``OP_*``:

1. Add the op to ``OPERATION_RECOVERY_CATEGORY`` with the appropriate
   category. Unknown ops fall through to ``drop_and_log`` (safe default,
   but the user gets no signal — explicit categorization is required by
   CC044-style discipline for queue ops; see lint rule CC044).
2. ``source_bound`` ops MUST carry ``data["source_id"]``.
3. ``chat_bound`` ops MUST carry ``data["chat_id"]``.
4. Both MUST carry ``data["database_name"]`` (or fall back to the
   ``metadata`` dict; both conventions are honored).

Recovery is best-effort: any failure (resource not found, DB error,
adapter unavailable) is logged and swallowed so queue processing
continues. The task itself is always marked ``failed/permanent`` by the
caller regardless of recovery outcome.
"""

from __future__ import annotations

from typing import Any, Literal

import structlog

from chaoscypher_core.constants import (
    OP_BUILD_GRAPH_SNAPSHOT,
    OP_CHAT_BACKGROUND,
    OP_CLEANUP_ORPHANS,
    OP_EMBED_CHUNKS,
    OP_EXTRACT_CHUNK,
    OP_FETCH_URL,
    OP_FINALIZE_EXTRACTION,
    OP_GRAPH_CLEANUP,
    OP_IMPORT_ANALYSIS,
    OP_IMPORT_CCX,
    OP_IMPORT_COMMIT,
    OP_IMPORT_INDEXING,
    OP_INDEX_DOCUMENT,
    OP_INDEX_IMPORTED_NODES,
    OP_INDEX_IMPORTED_SOURCE,
    OP_REBUILD_SEARCH_INDEXES,
    OP_RESET_ALL,
    OP_RESET_KNOWLEDGE_BASE,
    OP_VISION_FINALIZE,
    OP_VISION_PAGE,
)


logger = structlog.get_logger(__name__)


OperationCategory = Literal["source_bound", "chat_bound", "drop_and_log"]


# Every queue operation MUST appear here. Unknown ops fall through to
# ``drop_and_log`` semantics — safe (no resource corruption) but the user
# gets no retry prompt, so adding a new OP_* without registering its
# category is a soft regression.
OPERATION_RECOVERY_CATEGORY: dict[str, OperationCategory] = {
    # ---------- source_bound: data["source_id"] required ----------
    OP_IMPORT_INDEXING: "source_bound",
    OP_IMPORT_ANALYSIS: "source_bound",
    OP_EXTRACT_CHUNK: "source_bound",
    OP_FINALIZE_EXTRACTION: "source_bound",
    OP_VISION_PAGE: "source_bound",
    OP_VISION_FINALIZE: "source_bound",
    OP_IMPORT_COMMIT: "source_bound",
    OP_INDEX_DOCUMENT: "source_bound",
    OP_EMBED_CHUNKS: "source_bound",
    # Re-index an imported source (re-embed chunks + push vectors); carries
    # data["source_id"], so recovery marks that source for retry like any other
    # source-indexing op.
    OP_INDEX_IMPORTED_SOURCE: "source_bound",
    # ---------- chat_bound: data["chat_id"] required ----------
    OP_CHAT_BACKGROUND: "chat_bound",
    "chat_completion": "chat_bound",
    "tool_execution": "chat_bound",
    # ---------- drop_and_log: no owning resource, or idempotent ----------
    # OP_FETCH_URL creates the source row in its handler; an interrupted
    # fetch has no source row to mark, so the user just retries the URL.
    OP_FETCH_URL: "drop_and_log",
    # CCX backup imports create many sources; recovery would be ambiguous.
    OP_IMPORT_CCX: "drop_and_log",
    # Knowledge-only import indexing has no owning source (works off node_ids);
    # the nodes are already persisted and the ANN index is rebuildable, so an
    # interrupted run just needs a search rebuild, not a per-source retry.
    OP_INDEX_IMPORTED_NODES: "drop_and_log",
    # System / cross-cutting ops are idempotent or trigger-on-next-action.
    OP_REBUILD_SEARCH_INDEXES: "drop_and_log",
    OP_RESET_KNOWLEDGE_BASE: "drop_and_log",
    OP_RESET_ALL: "drop_and_log",
    OP_GRAPH_CLEANUP: "drop_and_log",
    OP_CLEANUP_ORPHANS: "drop_and_log",
    OP_BUILD_GRAPH_SNAPSHOT: "drop_and_log",
    "bulk_nodes": "drop_and_log",
    "bulk_edges": "drop_and_log",
    "bulk_templates": "drop_and_log",
    "export_graph": "drop_and_log",
    "export_by_sources": "drop_and_log",
    "lexicon_import": "drop_and_log",
    "execute_workflow": "drop_and_log",
    "execute_step": "drop_and_log",
    "recalculate_quality_scores": "drop_and_log",
    "regenerate_template_embeddings": "drop_and_log",
}


_UPGRADE_INTERRUPTED_MESSAGE = (
    "Interrupted by a worker upgrade (payload version mismatch). Click Retry to resume."
)


def _resolve_database_name(data: dict[str, Any], metadata: dict[str, Any]) -> str | None:
    """Find the database name on the task payload, honoring both conventions."""
    db = data.get("database_name")
    if isinstance(db, str) and db:
        return db
    md_db = metadata.get("database_name")
    if isinstance(md_db, str) and md_db:
        return md_db
    return None


def _recover_source_bound(
    data: dict[str, Any], metadata: dict[str, Any], operation: str, task_id: str
) -> None:
    """Mark the owning source as ERROR with an upgrade-interruption message."""
    source_id = data.get("source_id")
    database_name = _resolve_database_name(data, metadata)
    if not isinstance(source_id, str) or not source_id:
        logger.warning(
            "upgrade_recovery_missing_source_id",
            operation=operation,
            task_id=task_id,
        )
        return
    if not database_name:
        logger.warning(
            "upgrade_recovery_missing_database_name",
            operation=operation,
            task_id=task_id,
            source_id=source_id,
        )
        return

    # Lazy imports: keep this module light at import time and avoid pulling
    # the adapter chain into queue/__init__.py.
    from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
    from chaoscypher_core.models import SourceStatus

    adapter = get_sqlite_adapter(database_name)
    try:
        with adapter.transaction():
            session = adapter.session
            assert session is not None
            from chaoscypher_core.adapters.sqlite.models import SourceRow

            row = session.get(SourceRow, source_id)
            if row is None:
                logger.warning(
                    "upgrade_recovery_source_not_found",
                    operation=operation,
                    task_id=task_id,
                    source_id=source_id,
                )
                return
            row.status = SourceStatus.ERROR
            row.error_message = _UPGRADE_INTERRUPTED_MESSAGE
            row.error_stage = "upgrade_recovery"
            session.add(row)
        logger.info(
            "upgrade_recovery_source_marked_error",
            operation=operation,
            task_id=task_id,
            source_id=source_id,
        )
    except Exception as exc:
        logger.warning(
            "upgrade_recovery_source_update_failed",
            operation=operation,
            task_id=task_id,
            source_id=source_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
    finally:
        adapter.disconnect()


def _recover_chat_bound(
    data: dict[str, Any], metadata: dict[str, Any], operation: str, task_id: str
) -> None:
    """Mark the owning chat as ``error`` so the user can retry the message."""
    chat_id = data.get("chat_id") or metadata.get("chat_id")
    database_name = _resolve_database_name(data, metadata)
    if not isinstance(chat_id, str) or not chat_id:
        logger.warning(
            "upgrade_recovery_missing_chat_id",
            operation=operation,
            task_id=task_id,
        )
        return
    if not database_name:
        logger.warning(
            "upgrade_recovery_missing_database_name",
            operation=operation,
            task_id=task_id,
            chat_id=chat_id,
        )
        return

    from chaoscypher_core.database.adapter_factory import get_sqlite_adapter

    adapter = get_sqlite_adapter(database_name)
    try:
        with adapter.transaction():
            session = adapter.session
            assert session is not None
            from chaoscypher_core.adapters.sqlite.models import Chat

            row = session.get(Chat, chat_id)
            if row is None:
                logger.warning(
                    "upgrade_recovery_chat_not_found",
                    operation=operation,
                    task_id=task_id,
                    chat_id=chat_id,
                )
                return
            row.status = "error"
            session.add(row)
        logger.info(
            "upgrade_recovery_chat_marked_error",
            operation=operation,
            task_id=task_id,
            chat_id=chat_id,
        )
    except Exception as exc:
        logger.warning(
            "upgrade_recovery_chat_update_failed",
            operation=operation,
            task_id=task_id,
            chat_id=chat_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
    finally:
        adapter.disconnect()


def apply_upgrade_recovery(
    *,
    operation: str,
    data: dict[str, Any],
    metadata: dict[str, Any],
    task_id: str,
    payload_version: int,
) -> None:
    """Run the recovery action for an unsupported-version task.

    Categorizes ``operation`` via ``OPERATION_RECOVERY_CATEGORY`` and
    dispatches to the matching handler. Failures are logged and
    swallowed — queue processing is never blocked by recovery problems.
    """
    category = OPERATION_RECOVERY_CATEGORY.get(operation, "drop_and_log")
    logger.info(
        "upgrade_recovery_dispatch",
        operation=operation,
        task_id=task_id,
        payload_version=payload_version,
        category=category,
        registered=operation in OPERATION_RECOVERY_CATEGORY,
    )
    if category == "source_bound":
        _recover_source_bound(data, metadata, operation, task_id)
    elif category == "chat_bound":
        _recover_chat_bound(data, metadata, operation, task_id)
    # drop_and_log: the dispatch log line above is sufficient.


__all__ = [
    "OPERATION_RECOVERY_CATEGORY",
    "OperationCategory",
    "apply_upgrade_recovery",
]
