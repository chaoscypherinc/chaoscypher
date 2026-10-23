# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Supersession: a later memory on a single-valued relation closes the earlier fact.

Spec 2026-10-20 decision 6. A single-valued relation gives a subject one object at a time
(where someone lives, whom they are married to): when a memory states a new object, the
open fact with the old one is closed (``valid_to`` = the new memory's ``valid_from``,
``closed_by_source_id`` = the new memory) and both are kept. The set of such relations is
:data:`MEMORY_SINGLE_VALUED` plus what a domain's vocabulary flags; the universal vocabulary
is not edited (the extraction recipe hashes it).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.models import Edge
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
        RelationVocabulary,
    )

logger = structlog.get_logger(__name__)

MEMORY_SINGLE_VALUED: frozenset[str] = frozenset(
    {
        "spouse_of",
        "married_to",
        "lives_in",
        "located_in",
        "works_for",
        "works_at",
        "employed_by",
        "employee_of",
        "leader_of",
        "capital_of",
        "born_in",
        "died_in",
    }
)
"""The relations a memory supersedes on by default: the spec's list and the generic domain's
labels for the same facts (``located_in``, ``employee_of``, ``born_in``, ``died_in``)."""


def single_valued_labels(vocabulary: RelationVocabulary) -> frozenset[str]:
    """The single-valued relation labels: the memory path's own and the vocabulary's flagged ones."""
    return MEMORY_SINGLE_VALUED | vocabulary.single_valued_labels


@dataclass(frozen=True)
class Superseded:
    """One closure: the fact that was open, the fact that closed it."""

    subject: str
    label: str
    old_object: str
    old_by: str
    old_from: date
    new_object: str
    new_by: str
    new_from: date
    self_closed: bool
    """The arriving fact was the older one and closed itself (kept as history)."""


def edge_valid_from(repo: GraphRepositoryProtocol, edge_id: str) -> date | None:
    """An edge's ``valid_from`` as its members say it: the earliest dated member's, else None."""
    dates = [d for d in repo.source_valid_from(repo.edge_members(edge_id)).values() if d]
    return min(dates) if dates else None


@dataclass(frozen=True)
class _Dated:
    """One dated edge of a subject and label: its newest memory decides its place."""

    edge: Edge
    earliest: date
    newest: date
    created: str
    newest_source: str
    newest_by: str


def _created(fields: dict[str, Any]) -> str:
    """A source's ``created_at`` as sortable text ("" when unknown)."""
    value = fields.get("created_at")
    return value.isoformat() if isinstance(value, datetime) else ""


def _dated(repo: GraphRepositoryProtocol, edge: Edge) -> _Dated | None:
    """The edge with its members' dates, or None when no memory member dates it."""
    members = list(repo.edge_members(edge.id))
    fields = repo.memory_source_fields(members)
    dated = [(m, fields[m]) for m in members if m in fields and fields[m].get("valid_from")]
    if not dated:
        return None
    newest_id, newest = max(dated, key=lambda mf: (mf[1]["valid_from"], _created(mf[1])))
    return _Dated(
        edge=edge,
        earliest=min(f["valid_from"] for _, f in dated),
        newest=newest["valid_from"],
        created=_created(newest),
        newest_source=newest_id,
        newest_by=str(newest.get("memory_by") or ""),
    )


def recompute_single_valued(
    repo: GraphRepositoryProtocol, subject_id: str, label: str
) -> list[Superseded]:
    """Set every window of ``subject_id``'s ``label`` facts from their members; the new closures.

    The one decision of what is current (spec 2026-10-20 decision 6), run after a
    commit, a fold, a release: each dated fact's place is its newest memory's
    ``valid_from`` (then ``created_at``). The newest fact is open; every other is
    closed by the first fact after it (``valid_to`` = that fact's date,
    ``closed_by_source_id`` = its memory). A fact restated later than its
    replacement is current again, and the replacement closes. A timeless fact
    (a document's) is never closed and never closes. Returns one record per
    window that closed or changed its closer.
    """
    labels: dict[str, str] = {}

    def label_of(node_id: str) -> str:
        if node_id not in labels:
            node = repo.get_node(node_id, include_embedding=False)
            labels[node_id] = node.label if node is not None else node_id
        return labels[node_id]

    dated = [d for d in (_dated(repo, e) for e in repo.edges_from(subject_id, label)) if d]
    dated.sort(key=lambda d: (d.newest, d.created))
    closures: list[Superseded] = []
    for i, fact in enumerate(dated):
        closer = dated[i + 1] if i + 1 < len(dated) else None
        valid_to = closer.newest if closer else None
        closed_by = closer.newest_source if closer else None
        edge = fact.edge
        if (edge.valid_from, edge.valid_to, edge.closed_by_source_id) == (
            fact.earliest,
            valid_to,
            closed_by,
        ):
            continue
        repo.set_edge_validity(
            edge.id, valid_from=fact.earliest, valid_to=valid_to, closed_by_source_id=closed_by
        )
        if closer is not None and edge.closed_by_source_id != closed_by:
            record = Superseded(
                subject=label_of(subject_id),
                label=label,
                old_object=label_of(edge.target_node_id),
                old_by=fact.newest_by,
                old_from=fact.newest,
                new_object=label_of(closer.edge.target_node_id),
                new_by=closer.newest_by,
                new_from=closer.newest,
                self_closed=False,
            )
            closures.append(record)
            logger.info(
                "memory_fact_superseded",
                subject=record.subject,
                label=record.label,
                old=record.old_object,
                new=record.new_object,
            )
    return closures


def supersede_memory_edges(
    repo: GraphRepositoryProtocol, source_id: str, *, single_valued: Collection[str]
) -> list[Superseded]:
    """Recompute the windows of every single-valued fact the memory ``source_id`` touches.

    One record per closure the recompute made (:func:`recompute_single_valued`); a
    record whose closer is another memory than ``source_id`` is one the arriving
    fact lost to (``self_closed``).
    """
    pairs: list[tuple[str, str]] = []
    for edge_id in repo.member_edge_ids(source_id):
        edge = repo.get_edge(edge_id)
        if edge is None or edge.label not in single_valued:
            continue
        pair = (edge.source_node_id, edge.label)
        if pair not in pairs:
            pairs.append(pair)
    closures: list[Superseded] = []
    for subject_id, label in pairs:
        for record in recompute_single_valued(repo, subject_id, label):
            closed = next(
                (e for e in repo.edges_from(subject_id, label) if label_matches(repo, e, record)),
                None,
            )
            mine = closed is not None and source_id in repo.edge_members(closed.id)
            closures.append(
                Superseded(**{**record.__dict__, "self_closed": True}) if mine else record
            )
    return closures


def label_matches(repo: GraphRepositoryProtocol, edge: Edge, record: Superseded) -> bool:
    """Whether ``edge`` is the closed fact ``record`` describes (by its object's label)."""
    node = repo.get_node(edge.target_node_id, include_embedding=False)
    return node is not None and node.label == record.old_object and edge.valid_to is not None


__all__ = [
    "MEMORY_SINGLE_VALUED",
    "Superseded",
    "edge_valid_from",
    "recompute_single_valued",
    "single_valued_labels",
    "supersede_memory_edges",
]
