# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Where a fact or a chunk comes from, read from the graph: the provenance the memory tools render.

:class:`GraphProvenance` is the engine's
:class:`~chaoscypher_core.services.workflows.tools.engine.memory_render.ProvenanceLookup`.
A fact's provenance is that of one of its member sources (``graph_edge_sources``,
spec decision 2: ``by`` and the dates live on the source row only): a memory
member wins over every other member, the newest memory (by ``valid_from``,
then ``created_at``) among memories; else a document; else an imported
package (a pack). The edge's own validity window (migration 0020) gives the
dates, its ``valid_from`` falling back to the chosen memory's until the
member rebuild writes it. The quoted sentence is the first stored citation
sentence of the chosen source, else the edge's first, read the way the graph
blocks read it (``graphrag_blocks.first_sentence``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal

from chaoscypher_core.models import is_memory_source
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_blocks import (
    first_sentence,
)
from chaoscypher_core.services.workflows.tools.engine.memory_render import Provenance


if TYPE_CHECKING:
    from chaoscypher_core.bootstrap import Engine


PACK_SOURCE_TYPES: frozenset[str] = frozenset({"imported"})
"""``sources.source_type`` of a source an imported ``.ccx`` package wrote (the package importer's), tagged ``[pack NAME]``."""

_Kind = Literal["memory", "doc", "pack"]
_KIND_RANK: dict[str, int] = {"memory": 0, "doc": 1, "pack": 2}
"""Which member names a fact: a memory, else a document, else a pack."""


def source_kind(row: Mapping[str, Any]) -> _Kind:
    """A source row's provenance kind, from its ``source_type``."""
    if is_memory_source(row):
        return "memory"
    if row.get("source_type") in PACK_SOURCE_TYPES:
        return "pack"
    return "doc"


def _as_date(value: Any) -> date | None:
    """A stored date (an ISO ``YYYY-MM-DD`` string, a date or a datetime) as a date; None when unreadable."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _choose(
    members: Sequence[tuple[str, Mapping[str, Any]]],
) -> tuple[str, Mapping[str, Any] | None]:
    """The member ``(source id, row)`` that names a fact; ``("", None)`` for none.

    A memory over a document over a pack; among memories the newest
    ``valid_from`` (an undated one is the oldest), then the latest
    ``created_at``; among documents, or packs, the earliest ``created_at``.
    Ties fall to the source id, so the choice is the same every time.
    """
    memories = [m for m in members if source_kind(m[1]) == "memory"]
    if memories:
        return max(
            memories,
            key=lambda m: (
                _as_date(m[1].get("valid_from")) or date.min,
                str(m[1].get("created_at") or ""),
                m[0],
            ),
        )
    if not members:
        return "", None
    return min(
        members,
        key=lambda m: (_KIND_RANK[source_kind(m[1])], str(m[1].get("created_at") or ""), m[0]),
    )


def _provenance(
    row: Mapping[str, Any] | None,
    *,
    valid_from: date | None = None,
    valid_to: date | None = None,
    sentence: str | None = None,
    chunk_id: str | None = None,
) -> Provenance:
    """The provenance a source row gives; a missing row reads as an untitled document."""
    if row is None:
        return Provenance("doc", None, None, valid_from, valid_to, sentence, chunk_id)
    kind = source_kind(row)
    if kind == "memory":
        return Provenance(
            "memory",
            row.get("memory_by"),
            None,
            valid_from or _as_date(row.get("valid_from")),
            valid_to,
            sentence,
            chunk_id,
        )
    name = row.get("title") or row.get("filename")
    return Provenance(kind, None, name, valid_from, valid_to, sentence, chunk_id)


class GraphProvenance:
    """The provenance of facts and chunks, read from the engine's graph and source storage.

    One instance serves one tool call: source rows and edge provenances are
    kept for the instance's life, so a renderer asking for the same edge or
    source twice reads it once.
    """

    def __init__(self, engine: Engine) -> None:
        """Read through ``engine``'s ``graph_repository``, ``storage_adapter`` and ``database_name``."""
        self._graph = engine.graph_repository
        self._storage = engine.storage_adapter
        self._db = engine.database_name
        self._sources: dict[str, dict[str, Any] | None] = {}
        self._edges: dict[str, Provenance] = {}

    def _source(self, source_id: str) -> dict[str, Any] | None:
        """A source row, read once per lookup and cached."""
        if source_id not in self._sources:
            self._sources[source_id] = self._storage.get_source(source_id, self._db)
        return self._sources[source_id]

    def for_edge(self, edge_id: str) -> Provenance:
        """The provenance of the fact ``edge_id`` states (see the module docstring)."""
        return self.for_edges([edge_id])[edge_id]

    def for_edges(self, edge_ids: Iterable[str]) -> dict[str, Provenance]:
        """The provenance of each of ``edge_ids``, the citations of those not read yet in one batch."""
        wanted = list(dict.fromkeys(edge_ids))
        unread = [eid for eid in wanted if eid not in self._edges]
        if unread:
            cites: dict[str, list[dict[str, Any]]] = {}
            for row in self._storage.get_relationship_citations_batch(self._db, unread):
                cites.setdefault(str(row.get("edge_id")), []).append(row)
            for eid in unread:
                self._edges[eid] = self._edge(eid, cites.get(eid, []))
        return {eid: self._edges[eid] for eid in wanted}

    def _edge(self, edge_id: str, cites: Sequence[Mapping[str, Any]]) -> Provenance:
        """One edge's provenance from its members, its validity and its citation rows."""
        edge = self._graph.get_edge(edge_id)
        member_ids = list(self._graph.edge_members(edge_id))
        if not member_ids and edge is not None and edge.source_id:
            member_ids = [edge.source_id]  # an edge written before memberships: its home source
        members = [(sid, row) for sid in member_ids if (row := self._source(sid)) is not None]
        chosen_id, chosen = _choose(members)
        found = first_sentence([r for r in cites if r.get("source_id") == chosen_id]) or (
            first_sentence(cites)
        )
        return _provenance(
            chosen,
            valid_from=edge.valid_from if edge is not None else None,
            valid_to=edge.valid_to if edge is not None else None,
            sentence=found[0] if found else None,
            chunk_id=(found[1] or None) if found else None,
        )

    def for_chunk(self, chunk_id: str) -> Provenance:
        """The provenance of a chunk: its source's, with no sentence."""
        chunk = self._storage.get_chunk(chunk_id, self._db)
        source_id = str((chunk or {}).get("source_id") or "")
        row = self._source(source_id) if source_id else None
        return _provenance(row, chunk_id=chunk_id)


__all__ = ["PACK_SOURCE_TYPES", "GraphProvenance", "source_kind"]
