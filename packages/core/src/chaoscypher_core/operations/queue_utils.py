# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared queue submission utilities for import operations.

Provides common queue submission functions used by both the API-layer
OperationsService and the worker-layer ImportOperationsService. This
eliminates duplication and ensures parameter parity across layers.

**Metadata convention:** every enqueue helper requires an explicit
``database_name`` and sets it on the task metadata. Downstream
``cancel_by_metadata`` / ``task_exists_for_source`` use this to scope
cancel and reconciliation queries per database. Callers may supply
additional keys via ``extra_metadata`` (``user_id``, ``chat_id``,
``source_id``, etc.) — the helper merges those on top of the standard
fields (``database_name``, ``operation_type``, identifier).
"""

import base64
from typing import TYPE_CHECKING, Any

from chaoscypher_core.constants import (
    OP_CLEANUP_ORPHANS,
    OP_EMBED_CHUNKS,
    OP_FETCH_URL,
    OP_GRAPH_CLEANUP,
    OP_IMPORT_ANALYSIS,
    OP_IMPORT_CCX,
    OP_IMPORT_COMMIT,
    OP_INDEX_DOCUMENT,
    OP_REBUILD_SEARCH_INDEXES,
    OP_RESET_ALL,
    OP_RESET_KNOWLEDGE_BASE,
    QUEUE_LLM,
    QUEUE_OPERATIONS,
)
from chaoscypher_core.queue import queue_client


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter


def _build_metadata(
    *,
    database_name: str,
    operation_type: str,
    extra_metadata: dict[str, Any] | None = None,
    **base_fields: Any,
) -> dict[str, Any]:
    """Assemble a standard metadata dict for enqueue helpers.

    Ensures ``database_name`` and ``operation_type`` are always present.
    Caller-supplied ``extra_metadata`` wins over built-in fields so that
    explicit overrides (rare) are honored, but will not drop the
    required scoping keys unless the caller deliberately set them to
    ``None`` — in which case we still restore them.
    """
    merged: dict[str, Any] = {
        "database_name": database_name,
        "operation_type": operation_type,
        **base_fields,
    }
    if extra_metadata:
        merged.update(extra_metadata)
    # Never let an override erase the required scoping keys.
    merged["database_name"] = database_name
    merged["operation_type"] = operation_type
    return merged


async def queue_import_ccx(
    file_content: bytes,
    *,
    database_name: str,
    merge: bool = False,
    priority: int = 50,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    """Queue CCX file import operation.

    Args:
        file_content: CCX file content bytes.
        database_name: Target database for the import — required for
            cancel-by-metadata scoping.
        merge: Whether to merge with existing graph or replace.
        priority: Task priority (0-100, higher = more priority).
        extra_metadata: Extra keys to merge into the task metadata
            (e.g., ``user_id``).

    Returns:
        Task ID for tracking.

    """
    encoded_content = base64.b64encode(file_content).decode("utf-8")
    return await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation=OP_IMPORT_CCX,
        data={"file_content": encoded_content, "merge": merge},
        priority=priority,
        metadata=_build_metadata(
            database_name=database_name,
            operation_type=OP_IMPORT_CCX,
            extra_metadata=extra_metadata,
        ),
    )


async def queue_import_analysis(
    file_id: str,
    file_info: dict[str, Any],
    analysis_depth: str,
    *,
    database_name: str,
    generate_embeddings: bool = True,
    priority: int = 50,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    """Queue import file analysis operation.

    Args:
        file_id: Import file ID.
        file_info: File information dictionary.
        analysis_depth: Analysis depth level.
        database_name: Target database — required for cancel-by-metadata
            scoping.
        generate_embeddings: Generate embeddings for entities.
        priority: Task priority (0-100, higher = more priority).
        extra_metadata: Extra keys to merge into the task metadata.

    Returns:
        Task ID for tracking.

    """
    data: dict[str, Any] = {
        "file_id": file_id,
        "file_info": file_info,
        "analysis_depth": analysis_depth,
        "generate_embeddings": generate_embeddings,
    }

    return await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation=OP_IMPORT_ANALYSIS,
        data=data,
        priority=priority,
        metadata=_build_metadata(
            database_name=database_name,
            operation_type=OP_IMPORT_ANALYSIS,
            extra_metadata=extra_metadata,
            file_id=file_id,
            source_id=file_id,
        ),
    )


async def queue_import_commit(
    file_id: str,
    commit_data: dict[str, Any],
    file_info: dict[str, Any],
    adapter: SqliteAdapter,
    *,
    database_name: str,
    priority: int = 50,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    """Queue import commit operation (IDs-only queue payload).

    The large ``commit_data`` dict (entities,
    relationships, suggested templates — can be MB-scale for large
    documents) is persisted onto the source row via
    ``adapter.set_source_commit_payload`` BEFORE enqueue. The queue
    payload only carries the source id + file info, which keeps
    Valkey memory flat regardless of extraction size. The
    ``_import_commit_handler`` reads the payload back from the DB at
    dispatch time and clears it atomically with the commit
    transaction on success.

    Args:
        file_id: Source ID (doubles as commit payload key).
        commit_data: Data to commit to the graph. Persisted as the
            source's ``commit_payload`` TEXT column, not embedded in
            the queue message.
        file_info: File information dictionary.
        adapter: SQLite adapter — used to persist the commit payload
            before enqueueing. Required.
        database_name: Target database — required for cancel-by-metadata
            scoping and for the commit payload write.
        priority: Task priority (0-100, higher = more priority).
        extra_metadata: Extra keys to merge into the task metadata.

    Returns:
        Task ID for tracking.

    """
    adapter.set_source_commit_payload(file_id, commit_data, database_name)
    return await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation=OP_IMPORT_COMMIT,
        data={
            "file_id": file_id,
            "file_info": file_info,
        },
        priority=priority,
        metadata=_build_metadata(
            database_name=database_name,
            operation_type=OP_IMPORT_COMMIT,
            extra_metadata=extra_metadata,
            file_id=file_id,
            source_id=file_id,
        ),
    )


async def queue_import_indexing(
    file_id: str,
    file_info: dict[str, Any],
    *,
    database_name: str,
    priority: int = 50,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue document indexing (chunking + embeddings for RAG).

    This is a FAST operation (no LLM analysis, just chunking + embedding).
    Requires Valkey — the handler runs in the Neuron worker.

    Args:
        file_id: Import file ID.
        file_info: File information dictionary (filepath, file_type, filename).
        database_name: Target database — required for cancel-by-metadata
            scoping.
        priority: Task priority (0-100, higher = more priority).
        extra_metadata: Extra keys to merge into the task metadata.

    Returns:
        ``{"task_id": str, "status": "queued"}``.

    Raises:
        QueueUnavailableError: If queue server is not connected.

    """
    task_id = await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation=OP_INDEX_DOCUMENT,
        data={
            "file_id": file_id,
            "file_info": file_info,
        },
        priority=priority,
        metadata=_build_metadata(
            database_name=database_name,
            operation_type=OP_INDEX_DOCUMENT,
            extra_metadata=extra_metadata,
            file_id=file_id,
            source_id=file_id,
        ),
    )
    return {"task_id": task_id, "status": "queued"}


async def queue_fetch_url(
    url: str,
    *,
    options: dict[str, Any],
    database_name: str,
    priority: int = 50,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    """Queue an async URL fetch + import on the Operations queue.

    The /sources/url Cortex route used to perform the WebScraper fetch
    inline, holding the HTTP connection open for the full remote round
    trip. This helper hands the work to the worker so the route can
    return 202 immediately.

    Args:
        url: HTTP/HTTPS URL to fetch. Caller must pre-validate
            ``url_safety`` — this helper does not.
        options: Forwarded as kwargs to ``upload_file`` after the fetch
            (extraction_depth, generate_embeddings, forced_domain,
            skip_duplicates, content_filtering, ...).
        database_name: Target database — required for cancel-by-metadata
            scoping.
        priority: Task priority (0-100).
        extra_metadata: Extra keys merged into task metadata.

    Returns:
        The queue task id.
    """
    return await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation=OP_FETCH_URL,
        data={"url": url, "options": options},
        priority=priority,
        metadata=_build_metadata(
            database_name=database_name,
            operation_type=OP_FETCH_URL,
            extra_metadata=extra_metadata,
            origin_url=url,
        ),
    )


async def queue_embed_chunks(
    source_id: str,
    file_info: dict[str, Any],
    *,
    database_name: str,
    priority: int = 50,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    """Queue chunk-embedding operation on the LLM queue.

    This helper is used by ``handle_index_document`` after chunks have
    been persisted to hand off the LLM-bound embedding work to the LLM
    queue. The payload is ID-only — chunks are fetched from the database
    by the handler via ``adapter.list_unembedded_chunks`` — so the queue
    payload stays small regardless of document size.

    ``OP_EMBED_CHUNKS`` is idempotent: ``DocumentChunk.embedded_at`` is
    the resume checkpoint, so a re-dispatch after a crash re-embeds only
    the unembedded tail. Registering the handler with
    ``retry_on_crash=True`` is therefore safe.

    Args:
        source_id: Source being indexed (the chunks belong to this source).
        file_info: File metadata dict; ``auto_analyze`` is honored after
            embedding completes.
        database_name: Target database — required for cancel-by-metadata
            scoping. Without it, cancel_tasks_for_database cannot reach
            OP_EMBED_CHUNKS tasks.
        priority: Task priority (0-100, higher = more priority).
            Typically ``settings.priorities.background``; callers that
            indexed interactively can pass ``settings.priorities.interactive``.
        extra_metadata: Extra keys to merge into the task metadata on top
            of the standard fields (``database_name``, ``operation_type``,
            ``source_id``).

    Returns:
        Task ID for tracking.

    Raises:
        QueueUnavailableError: If queue server is not connected.
    """
    return await queue_client.enqueue_task(
        queue=QUEUE_LLM,
        operation=OP_EMBED_CHUNKS,
        data={
            "source_id": source_id,
            "file_info": file_info,
        },
        priority=priority,
        metadata=_build_metadata(
            database_name=database_name,
            operation_type=OP_EMBED_CHUNKS,
            extra_metadata=extra_metadata,
            source_id=source_id,
        ),
    )


async def queue_reset_knowledge_base(
    *,
    database_name: str,
    priority: int = 50,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    """Queue a knowledge-base reset.

    Deletes source data, clears the knowledge graph, wipes import files,
    and resets search indices for ``database_name``. This used to run
    synchronously on the API thread (could take 30+ seconds on large
    datasets); the queue handler now runs it on the worker so the API
    returns immediately with a task id.

    Args:
        database_name: Target database for the reset.
        priority: Task priority (0-100).
        extra_metadata: Extra keys for cancel-by-metadata (e.g. user_id).

    Returns:
        Task ID for tracking.

    """
    return await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation=OP_RESET_KNOWLEDGE_BASE,
        data={"database_name": database_name},
        priority=priority,
        metadata=_build_metadata(
            database_name=database_name,
            operation_type=OP_RESET_KNOWLEDGE_BASE,
            extra_metadata=extra_metadata,
        ),
    )


async def queue_reset_all(
    *,
    database_name: str,
    priority: int = 50,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    """Queue a nuclear reset (drop and recreate ``app.db``).

    Args:
        database_name: Target database for the nuclear reset.
        priority: Task priority (0-100).
        extra_metadata: Extra keys for cancel-by-metadata.

    Returns:
        Task ID for tracking.

    """
    return await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation=OP_RESET_ALL,
        data={"database_name": database_name},
        priority=priority,
        metadata=_build_metadata(
            database_name=database_name,
            operation_type=OP_RESET_ALL,
            extra_metadata=extra_metadata,
        ),
    )


async def queue_graph_cleanup(
    *,
    database_name: str,
    priority: int = 50,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    """Queue a corrupt-node graph cleanup pass.

    Args:
        database_name: Target database for the cleanup.
        priority: Task priority (0-100).
        extra_metadata: Extra keys for cancel-by-metadata.

    Returns:
        Task ID for tracking.

    """
    return await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation=OP_GRAPH_CLEANUP,
        data={"database_name": database_name},
        priority=priority,
        metadata=_build_metadata(
            database_name=database_name,
            operation_type=OP_GRAPH_CLEANUP,
            extra_metadata=extra_metadata,
        ),
    )


async def queue_cleanup_orphans(
    *,
    database_name: str,
    priority: int = 50,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    """Queue a cleanup pass for orphaned graph items.

    Args:
        database_name: Target database for the cleanup.
        priority: Task priority (0-100).
        extra_metadata: Extra keys for cancel-by-metadata.

    Returns:
        Task ID for tracking.

    """
    return await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation=OP_CLEANUP_ORPHANS,
        data={"database_name": database_name},
        priority=priority,
        metadata=_build_metadata(
            database_name=database_name,
            operation_type=OP_CLEANUP_ORPHANS,
            extra_metadata=extra_metadata,
        ),
    )


async def queue_rebuild_search_indexes(
    *,
    database_name: str,
    regenerate: bool = False,
    priority: int = 50,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    """Queue search index rebuild operation.

    Args:
        database_name: Target database — required for cancel-by-metadata
            scoping.
        regenerate: If True, regenerate embeddings from text before
            rebuilding (auto-detected by API, not user-facing).
        priority: Task priority (0-100, higher = more priority).
        extra_metadata: Extra keys to merge into the task metadata.

    Returns:
        Task ID for tracking.

    """
    return await queue_client.enqueue_task(
        queue=QUEUE_OPERATIONS,
        operation=OP_REBUILD_SEARCH_INDEXES,
        data={"regenerate": regenerate},
        priority=priority,
        metadata=_build_metadata(
            database_name=database_name,
            operation_type=OP_REBUILD_SEARCH_INDEXES,
            extra_metadata=extra_metadata,
        ),
    )
