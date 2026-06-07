# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Background maintenance for FTS5 + sqlite-vec consistency.

Runs on a timer (interval configured by settings.intervals.search_sweep_seconds,
default 300 s / 5 min).

Two responsibilities:

1. Orphan cleanup: delete fulltext_content and vec_search_nodes rows whose
   graph_nodes parent no longer exists.
2. Pending drain: retry indexing for rows in pending_search_index;
   delete on success, bump attempts and record last_error on failure.
   Once an entry crosses ``max_attempts`` failures, the queue row is
   removed and the owning source's ``vector_indexing_status`` flips to
   ``failed`` so the operator sees a "Search failed" badge in the UI
   (Workstream 10).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog
from sqlalchemy import text
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.models import (
    DocumentChunk,
    GraphNode,
    PendingSearchIndex,
)
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.models import Node, NodePosition
from chaoscypher_core.services.quality.counters import (
    mark_search_indexing_failed,
    mark_search_indexing_indexed,
)


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository

logger = structlog.get_logger(__name__)


def _graph_node_to_pydantic(db_node: GraphNode) -> Node:
    """Convert a GraphNode SQLModel entity to a Node Pydantic model.

    Args:
        db_node: SQLModel entity loaded from graph_nodes.

    Returns:
        Pydantic Node ready for SearchRepository.index_node.
    """
    position = None
    if db_node.position_x is not None and db_node.position_y is not None:
        position = NodePosition(x=db_node.position_x, y=db_node.position_y)
    return Node(
        id=db_node.id,
        template_id=db_node.template_id,
        label=db_node.label,
        properties=db_node.properties or {},
        position=position,
        embedding=db_node.embedding,
        source_id=db_node.source_id,
        created_at=db_node.created_at,
        updated_at=db_node.updated_at,
    )


def _reindex_chunks_for_source(
    source_id: str,
    adapter: SqliteAdapter,
    search_repo: SearchRepository,
) -> None:
    """Re-index all chunks belonging to source_id into vec_search_chunks.

    Mirrors CommitService._index_chunks_to_vector_search but operates
    directly on the adapter session.

    Args:
        source_id: The source ID whose chunks need re-indexing.
        adapter: Active SqliteAdapter.
        search_repo: SearchRepository to write vec rows into.

    Raises:
        Exception: Propagated so the caller records last_error.
    """
    session = adapter.session
    if session is None:  # pragma: no cover - caller passes a connected adapter
        msg = "SqliteAdapter must be connected before sweeping search indexes"
        raise RuntimeError(msg)
    stmt = select(DocumentChunk).where(DocumentChunk.source_id == source_id)
    chunks = list(session.exec(stmt))
    if not chunks:
        return
    embeddings_to_index: list[tuple[str, list[float]]] = []
    text_lookup: dict[str, str] = {}
    for chunk in chunks:
        if not chunk.embedding:
            continue
        embedding_bytes = base64.b64decode(chunk.embedding)
        embedding_array = np.frombuffer(embedding_bytes, dtype=np.float32)
        chunk_id = f"chunk:{chunk.id}"
        embeddings_to_index.append((chunk_id, embedding_array.tolist()))
        if chunk.content:
            text_lookup[chunk_id] = chunk.content
    if embeddings_to_index:
        search_repo.index_embeddings_batch(
            embeddings_to_index,
            item_type="chunk",
            text_lookup=text_lookup,
            session=session,
        )


def sweep_search_indexes(  # noqa: PLR0915 - sweeper orchestrates many index types in sequence; refactor out-of-scope
    adapter: SqliteAdapter,
    search_repo: SearchRepository,
    *,
    max_attempts: int,
) -> dict[str, int]:
    """Run a single sweep cycle.

    Performs both orphan cleanup and pending-index drain.  Designed to be
    called directly in tests and via asyncio.to_thread from the worker loop.

    Args:
        adapter: Connected SqliteAdapter.
        search_repo: SearchRepository sharing the same database file.
        max_attempts: Maximum retry attempts per pending entry before the
            sweep removes the queue row and flips the owning source's
            ``vector_indexing_status`` to ``"failed"``. Required parameter —
            must be passed by the caller from
            ``settings.intervals.search_sweep_max_attempts``. Removed module
            default to prevent silent settings shadow.

    Returns:
        Dict with observability counters: ``fts_orphans``, ``vec_orphans``,
        ``pending_drained``, ``pending_failed``, ``pending_exhausted``.
        ``pending_exhausted`` counts entries removed for crossing
        ``max_attempts``; the owning source row is transitioned to
        ``"failed"`` for each.
    """
    stats: dict[str, int] = {
        "fts_orphans": 0,
        "vec_orphans": 0,
        "pending_drained": 0,
        "pending_failed": 0,
        "pending_exhausted": 0,
    }

    session = adapter.session
    if session is None:  # pragma: no cover - caller passes a connected adapter
        msg = "SqliteAdapter must be connected before sweeping search indexes"
        raise RuntimeError(msg)

    # 1. Orphan cleanup — raw SQL so it runs as a single DELETE statement.
    try:
        r = session.exec(  # type: ignore[call-overload]
            text("DELETE FROM fulltext_content WHERE node_id NOT IN (SELECT id FROM graph_nodes)")
        )
        stats["fts_orphans"] = r.rowcount or 0
    except Exception:
        logger.exception("search_sweep_fts_orphan_cleanup_failed")

    try:
        r = session.exec(  # type: ignore[call-overload]
            text("DELETE FROM vec_search_nodes WHERE item_id NOT IN (SELECT id FROM graph_nodes)")
        )
        stats["vec_orphans"] = r.rowcount or 0
    except Exception:
        logger.exception("search_sweep_vec_orphan_cleanup_failed")

    session.commit()

    # 2. Pending drain — bounded batch per cycle to avoid long locks.
    batch_size = get_settings().batching.search_index_pending_batch_size
    stmt = select(PendingSearchIndex).limit(batch_size)
    pending = list(session.exec(stmt))

    for entry in pending:
        # Snapshot fields before mutation so we can transition the
        # owning source after the queue row is mutated/deleted.
        entry_source_id = entry.source_id
        try:
            if entry.kind == "node":
                db_node = session.get(GraphNode, entry.item_id)
                if db_node is None:
                    # Target gone — remove stale pending marker.
                    session.delete(entry)
                    session.commit()
                    stats["pending_drained"] += 1
                    if entry_source_id:
                        mark_search_indexing_indexed(
                            adapter=adapter,
                            source_id=entry_source_id,
                            database_name=adapter.database_name,
                        )
                    continue
                node = _graph_node_to_pydantic(db_node)
                search_repo.index_node(node, session=session)
            elif entry.kind == "chunk":
                # item_id for chunk kind == file_id == source_id on DocumentChunk.
                _reindex_chunks_for_source(entry.item_id, adapter, search_repo)
            else:
                logger.warning(
                    "search_sweep_unknown_kind",
                    kind=entry.kind,
                    item_id=entry.item_id,
                )
                continue
            session.delete(entry)
            session.commit()
            stats["pending_drained"] += 1
            # Recovered: the owning source moves from 'degraded' back to
            # 'indexed' so the UI badge clears.
            if entry_source_id:
                mark_search_indexing_indexed(
                    adapter=adapter,
                    source_id=entry_source_id,
                    database_name=adapter.database_name,
                )
        except Exception as exc:
            # Roll back partial writes from this entry so the session
            # stays usable for subsequent entries.
            with contextlib.suppress(Exception):
                session.rollback()
            entry.attempts += 1
            entry.last_error = str(exc)[:500]

            # Retry exhaustion: drop the queue row and mark the source
            # as terminally failed so the operator can act.
            if entry.attempts >= max_attempts:
                with contextlib.suppress(Exception):
                    session.delete(entry)
                    session.commit()
                stats["pending_exhausted"] += 1
                logger.warning(
                    "search_sweep_pending_entry_exhausted",
                    kind=entry.kind,
                    item_id=entry.item_id,
                    attempts=entry.attempts,
                    max_attempts=max_attempts,
                    error=str(exc)[:200],
                )
                if entry_source_id:
                    mark_search_indexing_failed(
                        adapter=adapter,
                        source_id=entry_source_id,
                        database_name=adapter.database_name,
                    )
                continue

            with contextlib.suppress(Exception):
                session.commit()
            stats["pending_failed"] += 1
            logger.warning(
                "search_sweep_pending_entry_failed",
                kind=entry.kind,
                item_id=entry.item_id,
                attempts=entry.attempts,
                error=str(exc)[:200],
            )

    logger.info("search_sweep_completed", **stats)
    return stats


async def _search_sweep_loop(
    adapter: Any,
    search_repo: Any,
    interval_seconds: int,
    *,
    max_attempts: int,
) -> None:
    """Periodic search-index sweep loop.

    Cancellable via asyncio.CancelledError. Matches the pattern of
    _source_recovery_loop and _orphan_task_cleanup_loop in worker.py.

    Args:
        adapter: SqliteAdapter instance.
        search_repo: SearchRepository instance.
        interval_seconds: Seconds between sweep cycles.
        max_attempts: Forwarded to ``sweep_search_indexes``; once an entry
            crosses this many failures, the sweep marks the owning source
            as terminally ``failed`` and removes the queue row.
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            # Per-pass session scope: sweep_search_indexes touches
            # adapter.session inside the worker thread, which inherits this
            # scope via asyncio.to_thread's context copy — so the sweep never
            # shares the singleton _fallback_session with concurrent loops or
            # queue handlers (the 2026-05-20 silent-data-loss race).
            async with adapter.session_scope():
                await asyncio.to_thread(
                    sweep_search_indexes,
                    adapter,
                    search_repo,
                    max_attempts=max_attempts,
                )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("search_sweep_loop_error")
