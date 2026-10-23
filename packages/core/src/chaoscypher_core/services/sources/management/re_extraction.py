# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Atomic force-re-extract — graph release + source-row reset in one transaction.

Audit fix #C3. Previously these were two separate commits on separate
sessions; a crash between them left a COMMITTED source with no graph
artifacts.

Both writes share a single SafeSession (``storage_adapter.session``) inside
``adapter.transaction()``: the graph release runs on a repository built on
that session (``get_transaction_graph_repository``), never on a caller's
repository that may hold another session. Because ``adapter.transaction()``
uses ``_transaction_depth > 0`` to defer the actual COMMIT until the context
manager exits cleanly, a crash between the two writes rolls back both — the
graph release and the source-row reset are fully atomic.

The release takes the source's memberships and only the nodes and edges no
other source attests (#795): a node merged with another source's survives,
rebuilt from the sources it still has (``release_source_graph``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.repo_factories import get_transaction_graph_repository
from chaoscypher_core.services.graph.management.release import release_source_graph


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.models import ReleasedRows
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


def force_re_extract(
    *,
    source_id: str,
    database_name: str,
    storage_adapter: SqliteAdapter,
    settings: EngineSettings | None,
) -> ReleasedRows:
    """Reset a committed source to INDEXED + release its graph rows atomically.

    Args:
        source_id: The source to re-extract.
        database_name: The database the source lives in.
        storage_adapter: Storage adapter (must expose ``transaction()``,
            ``session``, and ``reset_for_re_extraction``).
        settings: The engine settings the graph's naming conventions are read
            under (the same the delete and the commit pass), for the rebuild.

    Returns:
        What the release took from the graph and rebuilt. The caller runs
        ``release.refresh_after_release`` on it now, with its embedding
        provider: the re-extraction resets the commit counters, so the next
        commit skips its own cleanup and never sees this release.

    Raises:
        RuntimeError: If ``storage_adapter.session`` is ``None`` inside the
            transaction context (disconnected adapter).
        Whatever the underlying adapter / repository raise. The transaction
            context manager rolls back both writes on any exception.
    """
    with storage_adapter.transaction():
        released = release_source_graph(
            get_transaction_graph_repository(storage_adapter, database_name),
            source_id,
            sources=storage_adapter,
            database_name=database_name,
            settings=settings,
        )
        storage_adapter.reset_for_re_extraction(
            source_id=source_id,
            database_name=database_name,
        )
        logger.info(
            "force_re_extract_committed",
            source_id=source_id,
            database_name=database_name,
            nodes_deleted=len(released.deleted_node_ids),
            nodes_kept=len(released.touched_node_ids),
        )
    return released


__all__ = ["force_re_extract"]
