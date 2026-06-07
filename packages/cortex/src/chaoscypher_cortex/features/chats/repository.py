# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat scope repository.

Thin wrapper over the SqliteAdapter for the two data-access operations
the chats feature needs outside of the engine ``ChatService``:

- Resolving an explicit ``source_ids`` list plus any ``tag_ids`` into a
  final deduped scope (see :meth:`ChatScopeRepository.resolve_scope`).
- Looking up the display titles for a set of source ids in a single
  query so the ``PATCH /chats/{id}/scope`` handler can emit a
  human-readable "scope updated" system message
  (see :meth:`ChatScopeRepository.get_source_titles`).

The repository exists so the service layer and the route handlers never
need to touch ``adapter.transaction()`` / ``session.exec`` /
``load_only`` directly; all source-lookup logic lives behind the
adapter API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter


class ChatScopeRepository:
    """Source-scope data access for the chats feature.

    Responsibilities are intentionally narrow: combine explicit source
    ids with tag-derived source ids, and resolve source display titles
    in batch. Anything else belongs on the engine ``ChatService`` or a
    dedicated Cortex service.
    """

    def __init__(self, adapter: SqliteAdapter, database_name: str) -> None:
        """Initialize with an SqliteAdapter and target database name."""
        self.adapter = adapter
        self.database_name = database_name

    def resolve_scope(
        self,
        source_ids: list[str] | None,
        tag_ids: list[str] | None,
    ) -> list[str] | None:
        """Combine explicit ``source_ids`` with tag-derived source ids.

        Returns ``None`` when the combined, deduplicated set is empty
        (this is the API contract for "no scope"). Preserves the
        existing handler semantics, which is:

        - Start with ``source_ids`` as given (empty list if ``None``).
        - If ``tag_ids`` is truthy, ask the adapter for the source ids
          associated with those tags and union them in.
        - Dedupe the combined list.

        Note: order is NOT stable across calls because ``set`` is used
        for dedup. The handlers that previously inlined this logic had
        the same behavior.

        Args:
            source_ids: Explicit source ids from the caller.
            tag_ids: Optional tag ids whose sources should be included.

        Returns:
            A deduplicated list of source ids, or ``None`` if empty.

        """
        resolved: list[str] = list(source_ids) if source_ids else []

        if tag_ids:
            tag_source_ids = self.adapter.get_source_ids_by_tag_ids(tag_ids, self.database_name)
            resolved = list(set(resolved + tag_source_ids))

        return resolved if resolved else None

    def get_source_titles(self, source_ids: list[str]) -> list[str]:
        """Return display titles in the same order as ``source_ids``.

        Each entry is the source's ``title`` if set, else ``filename``,
        else the raw id (when the source is missing from the database).
        Uses a single batch query — no N+1.

        Args:
            source_ids: Source ids to resolve, in desired output order.

        Returns:
            List of display titles matching the input order.

        """
        if not source_ids:
            return []

        title_map = self.adapter.get_source_titles_by_ids(source_ids, self.database_name)
        return [title_map.get(sid, sid) for sid in source_ids]
