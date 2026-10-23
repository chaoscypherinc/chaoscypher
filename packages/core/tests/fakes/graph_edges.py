# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The graph port's batched edge listing, for a fake graph that lists edges one node at a time."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any


def edges_by_node_via(list_edges: Callable[..., Any]) -> Callable[..., dict[str, list[Any]]]:
    """A ``list_edges_by_node`` that asks ``list_edges`` for each node in turn.

    The port's contract is exactly that (``GraphRepositoryProtocol.list_edges_by_node``); the
    SQLite adapter reads it in one query per batch, and a fake keeps its ``list_edges``
    behaviour (filters, caps, recorded calls) by deriving the batch from it. Usable as a
    ``MagicMock`` side effect.
    """

    def list_edges_by_node(
        node_ids: Sequence[str],
        *,
        outgoing: bool,
        limit_per_node: int,
        include_disabled_sources: bool = False,
    ) -> dict[str, list[Any]]:
        key = "source_node_id" if outgoing else "target_node_id"
        return {
            node_id: list(list_edges(**{key: node_id, "limit": limit_per_node}))
            for node_id in node_ids
        }

    return list_edges_by_node


class EdgesByNode:
    """A fake graph's ``list_edges_by_node``, from its own ``list_edges`` (:func:`edges_by_node_via`)."""

    def list_edges_by_node(
        self,
        node_ids: Sequence[str],
        *,
        outgoing: bool,
        limit_per_node: int,
        include_disabled_sources: bool = False,
    ) -> dict[str, list[Any]]:
        """Each node's ``list_edges(source_node_id=...)`` (``target_node_id`` when incoming)."""
        listing: Callable[..., Any] = getattr(self, "list_edges")  # noqa: B009 - the fake's own
        return edges_by_node_via(listing)(
            node_ids,
            outgoing=outgoing,
            limit_per_node=limit_per_node,
            include_disabled_sources=include_disabled_sources,
        )
