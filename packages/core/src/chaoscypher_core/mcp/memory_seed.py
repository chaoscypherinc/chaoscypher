# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The seed of a memory's KNOWN ENTITIES list: the graph's nodes the text names, and the speaker.

A memory is one chunk, so the running list the document path builds group by group would
be empty for it. ``seed_records`` reads the graph instead (spec 2026-10-20 decision 2): the
nodes the keyword index returns for the text, the top hit of each ``about`` name, then the
speaker, so "I" and a bare first name land on the nodes the graph already has.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from chaoscypher_core.services.sources.engine.deduplication.name_identity import fold


if TYPE_CHECKING:
    from chaoscypher_core.models import Node


SPEAKER_ROLE = "the speaker (I, me)"
"""The role phrase the speaker's line carries, so the extractor reads "I" as that person."""


def _record(node: Node) -> dict[str, Any]:
    """A node as the shape ``KnownEntities.add_group`` reads."""
    aliases = node.properties.get("aliases") if isinstance(node.properties, dict) else None
    return {
        "name": node.label,
        "type": node.entity_type or "",
        "aliases": [str(a) for a in aliases or [] if a],
        "properties": {},
    }


def speaker_record(by: str) -> dict[str, Any]:
    """The speaker's record: a Person carrying :data:`SPEAKER_ROLE`."""
    return {"name": by, "type": "Person", "aliases": [], "properties": {"role": SPEAKER_ROLE}}


def seed_records(engine: Any, text: str, about: Sequence[str], by: str) -> list[dict[str, Any]]:
    """The seed records for a memory ``text`` said by ``by``, ``about`` the named people.

    At most ``extraction.known_entities_limit`` records; duplicates by folded name are
    dropped; the speaker is always last and always kept.
    """
    limit = engine.settings.extraction.known_entities_limit
    search = engine.search_repository
    hits: list[str] = []
    if text.strip():
        hits.extend(h for h, _ in search.keyword_search(text, limit=limit, item_type="node"))
    for name in about:
        hits.extend(h for h, _ in search.keyword_search(name, limit=1, item_type="node"))
    ids = list(dict.fromkeys(hits))
    nodes = engine.graph_repository.get_nodes_batch(ids, include_embedding=False) if ids else []
    by_id = {n.id: n for n in nodes}
    records: list[dict[str, Any]] = []
    seen = {fold(by)}
    for node_id in ids:
        node = by_id.get(node_id)
        if node is None or fold(node.label) in seen:
            continue
        seen.add(fold(node.label))
        records.append(_record(node))
    return [*records[: max(0, limit - 1)], speaker_record(by)]
