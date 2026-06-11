# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SearchRetryQueueProtocol — durable queue for deferred search re-indexing.

``PendingSearchIndex`` is adapter-owned scaffolding for deferred FTS5/vec
re-indexing after a failed commit-phase search write. The service layer
does not need to know the table structure — it just hands over
``{item_id, kind, source_id}`` triples inside a transaction.

Absorbs the SQLModel leak in
``packages/core/src/chaoscypher_core/services/sources/engine/commit/service.py::_enqueue_search_retry``
— the service-side call site goes through this protocol.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SearchRetryQueueProtocol(Protocol):
    """Persistent retry queue for deferred FTS5/vec re-indexing."""

    def enqueue_pending_search_index(self, *, rows: list[dict[str, Any]]) -> None:
        """Enqueue one or more items for deferred re-indexing.

        Semantics: ``INSERT OR IGNORE`` on a composite ``(kind, item_id)``
        unique key, so repeated failures of the same item produce exactly
        one queue row. Must be called inside an ``adapter.transaction()``
        context.

        Args:
            rows: List of dicts with keys:
                - ``item_id`` (str): ID of the graph node, chunk, or template
                - ``kind`` ("node" | "chunk" | "template")
                - ``source_id`` (str | None): Optional source reference
        """
        ...
