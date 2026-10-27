# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A scoped memory attaches to a node a person made, and never rewrites it.

A scoped memory's extraction is offered its scope's nodes by name (``mcp/memory_seed.py``,
steps 3 and 4). A node a person made (``create_node``: marked ``person_made``, migration 0025) has no
type, so the identity pass after the commit never compares it with the node the memory writes, and
"Trading journal" written by the memory would stay a second journal (spec 2026-10-24
§ 4.6, the trader case). :func:`join_offered_nodes` attaches the memory instead: its own
twin of the person's node is folded in by :func:`~chaoscypher_core.services.graph.resolution.apply.attach_node`,
so the memory attests the person's node and its edges move there, while the node keeps
the person's label, type and properties. Forgetting the memory takes its membership and
the edges only it attests; the node stays (a release never deletes a person-made row).

Only a twin the memory alone made is folded: homed at the memory, and attested by no
other source. A node a document or another memory also attests is never touched.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING

from chaoscypher_core.services.graph.resolution.apply import attach_node
from chaoscypher_core.services.sources.engine.deduplication.name_identity import fold


if TYPE_CHECKING:
    from chaoscypher_core.models import Node
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.search import SearchRepositoryProtocol
    from chaoscypher_core.services.graph.resolution.apply import ResolutionReport

OFFERED_NODES_KEY = "offered_nodes"
"""The commit data key holding the person-made scope nodes a memory may attach to."""


def attachable(node: Node) -> bool:
    """Whether a memory may attach to ``node``: a person made it and gave it no type.

    A typed person-made node is compared by the identity pass, which attaches to it
    (``resolution.apply``); one with no type never is, so the memory attaches here.
    """
    return node.person_made and not node.entity_type


def attachable_node_ids(nodes: Collection[Node]) -> list[str]:
    """The ids of the attachable nodes among a scoped memory's offered scope nodes."""
    return [n.id for n in nodes if attachable(n)]


def join_offered_nodes(
    repo: GraphRepositoryProtocol,
    source_id: str,
    offered: Collection[str],
    *,
    search: SearchRepositoryProtocol | None = None,
    report: ResolutionReport | None = None,
) -> int:
    """Attach the memory ``source_id`` to each person-made offered node it wrote by name.

    Args:
        repo: The graph.
        source_id: The memory source.
        offered: Ids of person-made scope nodes (:func:`attachable_node_ids`); one gone, or
            no longer person-made, is skipped.
        search: When given, the search index follows the removed twins.
        report: Filled as each attach commits.

    Returns:
        The number of twins folded. Idempotent: a second call finds none.
    """
    if not offered:
        return 0
    targets = {
        fold(n.label): n.id
        for n in repo.get_nodes_batch(sorted(set(offered)), include_embedding=False)
        if attachable(n)
    }
    if not targets:
        return 0
    joins = [
        (keep, node.id)
        for node in repo.get_nodes_batch(repo.member_node_ids(source_id), include_embedding=False)
        if (keep := targets.get(fold(node.label))) is not None
        and keep != node.id
        and node.source_id == source_id
        and set(repo.node_members(node.id)) == {source_id}
    ]
    for keep, twin in joins:
        attach_node(repo, keep, twin, only_from=source_id, search=search, report=report)
    return len(joins)


__all__ = [
    "OFFERED_NODES_KEY",
    "attachable",
    "attachable_node_ids",
    "join_offered_nodes",
]
