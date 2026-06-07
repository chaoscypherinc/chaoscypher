# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SQLite implementation of SearchRetryQueueProtocol.

Wraps the ``PendingSearchIndex`` table with ``INSERT OR IGNORE`` semantics
so the commit pipeline can enqueue deferred re-indexing items without
importing SQLModel or SQLAlchemy dialect code.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import PendingSearchIndex
from chaoscypher_core.ports.search_retry import SearchRetryQueueProtocol


class SearchRetryQueueMixin(SqliteMixinBase, SearchRetryQueueProtocol):
    """INSERT OR IGNORE pending-search-index rows for durable retry."""

    def enqueue_pending_search_index(self, *, rows: list[dict[str, Any]]) -> None:
        """Enqueue items for deferred re-indexing.

        See :class:`~chaoscypher_core.ports.search_retry.SearchRetryQueueProtocol`
        for semantics.
        """
        if not rows:
            return
        self._ensure_connected()
        for row in rows:
            item_id = row["item_id"]
            kind = row["kind"]
            source_id = row.get("source_id")
            stmt = (
                sqlite_insert(PendingSearchIndex)
                .values(
                    id=f"{kind}:{item_id}",
                    kind=kind,
                    item_id=item_id,
                    source_id=source_id,
                )
                .prefix_with("OR IGNORE")
            )
            self.session.execute(stmt)
        self._maybe_commit()
