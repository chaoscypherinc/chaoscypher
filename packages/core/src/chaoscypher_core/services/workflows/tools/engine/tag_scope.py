# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A retrieval's document-tag scope as a source list, and the answer for a scope that holds nothing.

``graphrag_search`` scopes by source ids; a tag scope becomes the ids of the
sources carrying the tags (the search repository's one tag lookup),
intersected with the caller's source ids, handed back as a :class:`SourceScope`
whose ``attested_only`` says it came from tags: strict on graph nodes and
relationships (only what a tagged document attests; owner decision
2026-11-05), where a plain source list also admits person-made and
unattested nodes. A tag scope that holds no
document is an empty list, answered without any retrieval: it never widens
to the whole graph.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, NamedTuple

from chaoscypher_core.services.sources.tags import filter_tag_scope


if TYPE_CHECKING:
    from chaoscypher_core.ports.search import SearchRepositoryProtocol


class SourceScope(NamedTuple):
    """A retrieval's source scope and whether its graph nodes must be attested by it.

    ``attested_only`` is True for a scope that came from document tags: every
    graph node and relationship must be attested by one of ``source_ids``
    (owner decision 2026-11-05). A plain source scope leaves it False and
    keeps person-made and unattested nodes. Carried together so a caller that
    converts tags cannot drop the strictness.
    """

    source_ids: list[str] | None
    attested_only: bool


def tag_source_scope(
    search: SearchRepositoryProtocol,
    source_ids: Sequence[str] | None,
    tags: Sequence[object] | None,
    match: str,
) -> SourceScope:
    """The source scope of a retrieval given ``source_ids`` and a tag scope.

    Args:
        search: The search repository; its ``tagged_source_ids`` is read only with tags.
        source_ids: The caller's source scope; None or empty for every source.
        tags: Document tags (``match``: ``any`` or ``all`` of them); None or
            empty for no tag scope.
        match: ``any`` or ``all``.

    Returns:
        A :class:`SourceScope`: ``source_ids`` unchanged and not
        ``attested_only`` without tags; with tags, the tagged sources (in
        ``source_ids``' order when it is given, intersected with it, an empty
        list when none is left) and ``attested_only``.

    Raises:
        TagRuleError: a tag breaks the rule, or ``match`` is neither ``any`` nor ``all``.

    """
    tag_names, tag_match = filter_tag_scope(tags or [], match)
    if not tag_names:
        return SourceScope(list(source_ids) if source_ids is not None else None, False)
    tagged = search.tagged_source_ids(tag_names, tag_match)
    if not source_ids:
        return SourceScope(list(tagged), True)
    held = set(tagged)
    return SourceScope([sid for sid in dict.fromkeys(source_ids) if sid in held], True)


def empty_scope_result(query: str, stats: dict[str, Any]) -> dict[str, Any]:
    """``graphrag_search``'s answer for a scope that holds no document: no retrieval ran.

    The usual keys, no chunks, an empty graph context and ``context_text``,
    and ``retrieval_stats["scope_empty"]`` (on ``stats``, the retrieval's
    initial stats) so a caller can tell it from a miss.
    """
    stats.update({"scope_empty": True, "shape": None, "faults": []})
    return {
        "success": True,
        "graph_context": {},
        "chunks": [],
        "retrieval_stats": stats,
        "shape": None,
        "query": query,
        "context_text": "",
    }


__all__ = ["SourceScope", "empty_scope_result", "tag_source_scope"]
