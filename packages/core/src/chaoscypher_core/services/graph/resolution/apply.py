# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Apply a :class:`ResolutionPlan` to a graph through its repository.

A merge folds the dropped node into the kept one: the dropped node's label
and aliases become aliases of the kept node (acquired, not given: the kept
node's ``aliases_given`` stay its own), its properties are merged under
the same policy the pre-commit deduplication uses
(:func:`merge_node_properties`), every edge on it is moved to the kept node
(an edge the kept node already has is folded into the kept node's copy, its
justification kept in ``justifications`` and the unevidenced flag kept only
when both copies carry it: ``merge_edge_properties``; one that would loop back
on itself is dropped, with its citations), and the kept node records
``resolved_from``. The dropped node's source memberships and citations move to
the kept node, a moved or folded edge's to its new edge (#795, spec 2026-10-17
D2/D4), so deleting either source later knows the merged node is attested
twice. An alias drop rewrites the node's alias list. A flag writes
``resolution_flag`` onto the edge and nothing else: flagged edges are a
reader's list, never deleted.

The kept node's embedding is not refreshed here: a reference pack re-embeds
every node when it is indexed, and the post-commit pass passes its own
re-embedder.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.models import Edge, EdgeCreate, EdgeUpdate, Node, NodeCreate, NodeUpdate
from chaoscypher_core.services.graph.resolution.plan import (
    ResolutionPlan,
    fold,
    member_order,
    node_given_aliases,
    plan_resolution,
)
from chaoscypher_core.services.sources.engine.deduplication.embedding_generator import (
    NODE_PROVENANCE_KEYS,
)
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    ATTESTED_NAMES_KEY,
    GIVEN_ALIASES_KEY,
    MERGED_NAMES_KEY,
    NameConventions,
    record_attested_names,
    record_merged_names,
)
from chaoscypher_core.services.sources.engine.deduplication.service import (
    merge_edge_properties,
    merge_node_properties,
)


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.search import SearchRepositoryProtocol


PAGE = 1000
RESOLVED_FROM = "resolved_from"
FLAG_KEY = "resolution_flag"
ABSORBED_KEY = "absorbed"
"""Membership snapshot key: what the same source wrote for the nodes (edges) merged into this one.

A source's snapshot is :func:`node_record` (or :func:`edge_record`) of what it
wrote. When the kept and the dropped side of a merge both hold a membership
for one source, the kept snapshot stays and the dropped one is appended under
this key (spec 2026-10-17 D2): a source's two readings of one entity are both
its own. The list is flat; an absorbed snapshot carries no list of its own.
"""


@dataclass
class ResolutionReport:
    """What :func:`apply_resolution` did, in counts and names."""

    merged: list[tuple[str, str]] = field(default_factory=list)
    """``(kept label, dropped label)`` per merge."""
    edges_moved: int = 0
    edges_dropped: int = 0
    """Edges on a dropped node the kept node already had, or that looped back on it."""
    edges_folded: int = 0
    """Of ``edges_dropped``, the ones the kept node already had: their properties folded into
    its copy (``merge_edge_properties``), their justification kept (keep, flag, never delete)."""
    aliases_dropped: list[tuple[str, str]] = field(default_factory=list)
    """``(node label, alias)`` per drop."""
    flagged: int = 0
    removed_edge_ids: set[str] = field(default_factory=set)
    """Every edge a merge took off the graph: moved (its copy has a new id), folded or looped."""
    removed_node_ids: set[str] = field(default_factory=set)
    """Every node a merge took off the graph (the dropped side)."""
    changed_node_ids: set[str] = field(default_factory=set)
    """Every surviving node whose text changed: a kept node after its merge, a node that lost
    an alias. With :attr:`removed_node_ids`, the only nodes whose search rows must follow."""

    def absorb(self, other: ResolutionReport) -> None:
        """Add what ``other`` (one committed unit of work) did to this report."""
        self.merged.extend(other.merged)
        self.edges_moved += other.edges_moved
        self.edges_dropped += other.edges_dropped
        self.edges_folded += other.edges_folded
        self.aliases_dropped.extend(other.aliases_dropped)
        self.flagged += other.flagged
        self.removed_edge_ids |= other.removed_edge_ids
        self.removed_node_ids |= other.removed_node_ids
        self.changed_node_ids |= other.changed_node_ids

    def summary(self) -> str:
        """One line for a log or a console."""
        return (
            f"{len(self.merged)} merged, {self.edges_moved} edges moved, "
            f"{self.edges_dropped} dropped ({self.edges_folded} folded), "
            f"{len(self.aliases_dropped)} aliases dropped, "
            f"{self.flagged} flagged"
        )


def node_record(node: Node) -> dict[str, Any]:
    """The planner's view of a repository node."""
    props = dict(node.properties or {})
    record: dict[str, Any] = {
        "id": node.id,
        "label": node.label,
        "type": node.entity_type or "",
        "aliases": [str(a) for a in props.get("aliases") or [] if a],
        "properties": props,
    }
    if isinstance(props.get(GIVEN_ALIASES_KEY), list):
        record[GIVEN_ALIASES_KEY] = [str(a) for a in props[GIVEN_ALIASES_KEY] if a]
    return record


def edge_record(edge: Edge) -> dict[str, Any]:
    """The planner's view of a repository edge."""
    return {
        "id": edge.id,
        "source": edge.source_node_id,
        "target": edge.target_node_id,
        "label": edge.label,
        "properties": dict(edge.properties or {}),
    }


def node_create_snapshot(node_id: str, create: NodeCreate) -> dict[str, Any]:
    """What a source writes for node ``node_id`` with ``create``: the planner's record, no embedding."""
    return node_record(
        Node(
            id=node_id,
            template_id=create.template_id,
            label=create.label,
            entity_type=create.entity_type,
            properties=create.properties or {},
            source_id=create.source_id,
        )
    )


def edge_create_snapshot(edge_id: str, create: EdgeCreate) -> dict[str, Any]:
    """What a source writes for edge ``edge_id`` with ``create``: the planner's record."""
    return edge_record(
        Edge(
            id=edge_id,
            template_id=create.template_id,
            source_node_id=create.source_node_id,
            target_node_id=create.target_node_id,
            label=create.label,
            properties=create.properties or {},
        )
    )


def snapshot_reading(snapshot: dict[str, Any]) -> dict[str, Any]:
    """What one source wrote, from its membership snapshot: no absorbed list, no ``resolved_from``.

    ``resolved_from`` is a merge's, never the source's: ``_move_edges`` writes it
    on the edge it re-creates, which is snapshotted with it.
    """
    out = {k: v for k, v in snapshot.items() if k != ABSORBED_KEY}
    props = out.get("properties")
    if isinstance(props, dict):
        out["properties"] = {k: v for k, v in props.items() if k != RESOLVED_FROM}
    return out


def member_readings(members: dict[str, dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Every reading a node's (edge's) member sources hold: ``(source_id, record)``, in one order.

    ``members`` maps source id to snapshot (``node_members`` / ``edge_members``).
    Each snapshot is one reading, and each entry of its :data:`ABSORBED_KEY`
    list another by the same source (:func:`snapshot_reading` of each). Sources
    in id order, a source's own snapshot before what it absorbed.
    """
    readings: list[tuple[str, dict[str, Any]]] = []
    for sid in sorted(members):
        snapshot = members[sid]
        readings.append((sid, snapshot_reading(snapshot)))
        readings.extend((sid, snapshot_reading(dict(r))) for r in snapshot.get(ABSORBED_KEY) or [])
    return readings


def _reading_content(record: dict[str, Any]) -> str:
    """What a reading says, as a comparable key: no id, no provenance properties.

    The provenance a commit or an import stamps on what a source wrote
    (:data:`NODE_PROVENANCE_KEYS`: which document, when, how) differs between
    two sources that wrote the same thing; it is not what they wrote.
    """
    props = {
        k: v for k, v in (record.get("properties") or {}).items() if k not in NODE_PROVENANCE_KEYS
    }
    rest = {k: v for k, v in record.items() if k not in ("id", "properties")}
    return json.dumps([rest, props], sort_keys=True, default=str)


def distinct_readings(
    readings: Sequence[tuple[str, dict[str, Any]]], *, prefer: str | None = None
) -> list[tuple[str, dict[str, Any], int]]:
    """``readings`` with each content once: ``(source_id, record, how many sources wrote it)``.

    Two sources that wrote the same thing (the sources of one CCX bundle citing
    one entity) are one reading: folding it into itself would record merge
    provenance (``resolved_from``, ``merged_names``) of the node's own label,
    as if it had been merged with itself. The count is the reading's support,
    so a name more sources write still ranks higher. Of the sources holding one
    content, ``prefer`` (the row's current home) is kept when it is among them,
    else the first in ``readings``' order; first occurrences keep their order.
    """
    kept: dict[str, list[Any]] = {}
    for sid, record in readings:
        key = _reading_content(record)
        entry = kept.get(key)
        if entry is None:
            kept[key] = [sid, record, 1]
            continue
        entry[2] += 1
        if sid == prefer and entry[0] != prefer:
            entry[0], entry[1] = sid, record
    return [(sid, record, count) for sid, record, count in kept.values()]


def load_graph(repo: GraphRepositoryProtocol) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every node and edge of the graph, paged, without embeddings."""
    nodes: list[dict[str, Any]] = []
    skip = 0
    while True:
        page = repo.list_nodes(skip=skip, limit=PAGE, include_embedding=False)
        nodes.extend(node_record(n) for n in page)
        if len(page) < PAGE:
            break
        skip += PAGE
    edges: list[dict[str, Any]] = []
    skip = 0
    while True:
        edge_page = repo.list_edges(skip=skip, limit=PAGE)
        edges.extend(edge_record(e) for e in edge_page)
        if len(edge_page) < PAGE:
            break
        skip += PAGE
    return nodes, edges


def _aliases_after(
    keep: dict[str, Any], dropped: Sequence[dict[str, Any]], removed: set[str]
) -> list[str]:
    """The kept record's aliases once the dropped records fold in, as one list without repeats.

    A dropped node's own name is always one of them, whatever alias drop is
    planned on the group: an alias drop takes off a name that is someone
    else's, and a node folded in here is this node (until 2026-10-07 a drop
    whose text was a dropped node's label took that name off the merged node).
    """
    seen: set[str] = {fold(keep["label"])}
    out: list[str] = []
    labels = {fold(d["label"]) for d in dropped}
    candidates = [*(keep["properties"].get("aliases") or [])]
    for d in dropped:
        candidates.append(d["label"])
        candidates.extend(d["properties"].get("aliases") or [])
    for alias in candidates:
        text = str(alias).strip()
        key = fold(text)
        if not text or key in seen or (key in removed and key not in labels):
            continue
        seen.add(key)
        out.append(text)
    return out


def fold_members(
    keep: dict[str, Any], dropped: Sequence[dict[str, Any]], removed: set[str]
) -> dict[str, Any]:
    """The properties of ``keep`` once ``dropped`` fold into it: the one fold of a merge group.

    ``keep`` and ``dropped`` are planner records (:func:`node_record`, or a
    source's membership snapshot, which is one); ``removed`` holds the folded
    alias texts an alias drop takes off the group. A merge (``_apply_merges``)
    and the rebuild of a node that lost a source
    (``management.release.release_source_graph``) both fold through here, so
    the two cannot drift apart (#795, spec 2026-10-17 D3). The dropped records
    fold in :func:`member_order`, whatever order they are given in. The label
    is not folded: it is ``keep``'s, chosen by the survivor ranking
    (``plan._keep``).

    The dropped labels and aliases become aliases of ``keep``, acquired, not
    given (``aliases_given`` stays ``keep``'s own); properties merge under the
    deduplication's policy (:func:`merge_node_properties`); the merged and
    attested names are recorded, and ``resolved_from`` lists the dropped labels.
    The provenance properties a commit or an import stamps
    (:data:`NODE_PROVENANCE_KEYS`: ``source_document_id`` and the like) are
    ``keep``'s alone: a rebuild homed at ``keep``'s source names that source.
    """
    dropped = sorted(dropped, key=member_order)
    props = dict(keep["properties"] or {})
    given = [a for a in node_given_aliases(keep) if fold(a) not in removed]
    for d in dropped:
        props = merge_node_properties(props, dict(d["properties"] or {}))
    props["aliases"] = _aliases_after(keep, dropped, removed)
    props[GIVEN_ALIASES_KEY] = given  # the dropped names are acquired, never given
    props[MERGED_NAMES_KEY] = list(
        dict.fromkeys(
            [
                *record_merged_names(keep),
                *(n for d in dropped for n in (d["label"], *record_merged_names(d))),
            ]
        )
    )
    attested = frozenset().union(*(record_attested_names(n) for n in (keep, *dropped)))
    if attested:  # the spellings the corpus writes, for a later pass's survivor ranking
        props[ATTESTED_NAMES_KEY] = sorted(attested)
    props[RESOLVED_FROM] = [*props.get(RESOLVED_FROM, []), *(d["label"] for d in dropped)]
    # provenance (which document, when, how) is keep's own, never another reading's
    own = dict(keep["properties"] or {})
    for key in NODE_PROVENANCE_KEYS:
        if key in own:
            props[key] = own[key]
        else:
            props.pop(key, None)
    return props


def _move_edges(
    repo: GraphRepositoryProtocol, keep: str, drop: str, report: ResolutionReport
) -> None:
    """Re-point every edge on ``drop`` at ``keep``; skip loops, fold an edge ``keep`` already has.

    An edge whose ``(source, target, label)`` the kept node already has is
    the same fact twice: it is not moved, and its properties fold into the
    kept node's copy (``merge_edge_properties``: its justification is kept in
    ``justifications``, the unevidenced flag stays only when both copies carry
    it, and an evidenced copy's sentence leads), so no evidence sentence is
    lost (keep, flag, never delete, 2026-10-07; until then the copy was
    dropped with its text: 3 edges on the v4b literary pack, 2 on the generic).
    A self-loop carries no fact between two nodes and is dropped.
    """
    have: dict[tuple[str, str, str], Edge] = {}
    for e in [
        *repo.list_edges(source_node_id=keep, limit=PAGE),
        *repo.list_edges(target_node_id=keep, limit=PAGE),
    ]:
        have.setdefault((e.source_node_id, e.target_node_id, e.label), e)
    for e in [
        *repo.list_edges(source_node_id=drop, limit=PAGE),
        *repo.list_edges(target_node_id=drop, limit=PAGE),
    ]:
        report.removed_edge_ids.add(e.id)  # whatever happens below, this row goes
        src = keep if e.source_node_id == drop else e.source_node_id
        tgt = keep if e.target_node_id == drop else e.target_node_id
        key = (src, tgt, e.label)
        if src == tgt:
            repo.drop_edge_citations(e.id)  # the fact they cite is gone (D4)
            report.edges_dropped += 1
            continue
        kept = have.get(key)
        if kept is not None:
            merged = merge_edge_properties(dict(kept.properties or {}), dict(e.properties or {}))
            if merged != dict(kept.properties or {}):
                updated = repo.update_edge(kept.id, EdgeUpdate(properties=merged))
                if updated is not None:
                    have[key] = updated
            if kept.valid_to is None and e.valid_to is not None:
                # the same fact, closed on the dropped row: the kept row takes the window
                repo.set_edge_validity(
                    kept.id,
                    valid_from=kept.valid_from or e.valid_from,
                    valid_to=e.valid_to,
                    closed_by_source_id=e.closed_by_source_id,
                )
            repo.absorb_edge(kept.id, e.id)
            report.edges_dropped += 1
            report.edges_folded += 1
            continue
        props = dict(e.properties or {})
        props[RESOLVED_FROM] = drop
        moved = repo.create_edge(
            EdgeCreate(
                template_id=e.template_id,
                source_node_id=src,
                target_node_id=tgt,
                label=e.label,
                properties=props,
                source_id=e.source_id,  # still what its home source wrote
            )
        )
        repo.absorb_edge(moved.id, e.id)
        if e.valid_from or e.valid_to or e.closed_by_source_id:
            # the moved fact keeps its validity window (a closed home stays closed, 2026-10-20)
            repo.set_edge_validity(
                moved.id,
                valid_from=e.valid_from,
                valid_to=e.valid_to,
                closed_by_source_id=e.closed_by_source_id,
            )
        have[key] = moved
        report.edges_moved += 1
    # delete_node cascades the old edges


def _apply_merges(
    repo: GraphRepositoryProtocol,
    plan: ResolutionPlan,
    removed_by_node: dict[str, set[str]],
    report: ResolutionReport,
    reembed: Callable[[Node], None] | None,
) -> None:
    """Fold every dropped node into its kept node; drops planned inside the group are consumed.

    Each group is one unit of work under the writer lock (``write_locked``):
    the kept and dropped nodes are read again once the lock is held, and the
    fold is built from those reads. The plan was made from a load taken
    earlier, outside any lock; a source release may have rebuilt or deleted
    the nodes since, and a fold of the earlier records would write the
    released source's names back (#795). A group whose kept node, or every
    dropped node, is gone is skipped.

    A group reaches ``report`` and ``reembed`` only once its unit has
    committed, so a failure in a later group leaves ``report`` naming exactly
    the groups that are in the graph (#795 follow-up).
    """
    groups: dict[str, list[str]] = {}
    for m in plan.merges:
        groups.setdefault(m.keep, []).append(m.drop)
    for keep_id, drop_ids in groups.items():
        done = ResolutionReport()
        changed: list[Node] = []
        with repo.write_locked():
            _merge_group(repo, plan, keep_id, drop_ids, removed_by_node, done, changed.append)
        _committed(report, done, changed, reembed)


def _committed(
    report: ResolutionReport,
    done: ResolutionReport,
    changed: list[Node],
    reembed: Callable[[Node], None] | None,
) -> None:
    """A unit of work committed: its counts and ids join ``report``, its changed nodes are re-embedded."""
    report.absorb(done)
    if reembed is not None:
        for node in changed:
            reembed(node)


def _merge_group(
    repo: GraphRepositoryProtocol,
    plan: ResolutionPlan,
    keep_id: str,
    drop_ids: list[str],
    removed_by_node: dict[str, set[str]],
    report: ResolutionReport,
    reembed: Callable[[Node], None] | None,
) -> None:
    """One merge group of :func:`_apply_merges`, inside its write lock."""
    keep = repo.get_node(keep_id, include_embedding=False)
    dropped = [n for n in (repo.get_node(d, include_embedding=False) for d in drop_ids) if n]
    if keep is None or not dropped:
        return
    # Fold the dropped nodes in one order, what their records are (``member_order``),
    # never the order the plan lists them in: the merged description is the same text
    # whatever ids the nodes carry (audit wave 8).
    dropped.sort(key=lambda n: member_order(node_record(n)))
    # An alias drop planned on any member stays applied only when its owner is outside
    # the group: "Anna Mikháylovna" on the dropped twin names the kept node, and is hers.
    group = {keep.id, *(d.id for d in dropped)}
    removed = {fold(d.alias) for d in plan.alias_drops if d.node in group and d.owner not in group}
    for n in (keep, *dropped):
        removed_by_node.pop(n.id, None)
    props = fold_members(node_record(keep), [node_record(d) for d in dropped], removed)
    for d in dropped:
        _move_edges(repo, keep.id, d.id, report)
        repo.absorb_node(keep.id, d.id)  # its sources and citations, before it cascades
        repo.delete_node(d.id)
        report.merged.append((keep.label, d.label))
        report.removed_node_ids.add(d.id)
    updated = repo.update_node(keep.id, NodeUpdate(properties=props))
    report.changed_node_ids.add(keep.id)
    if reembed is not None and updated is not None:
        reembed(updated)


def _apply_alias_drops(
    repo: GraphRepositoryProtocol,
    removed_by_node: dict[str, set[str]],
    report: ResolutionReport,
    reembed: Callable[[Node], None] | None,
) -> None:
    """Rewrite the alias list of every surviving node that has drops left.

    The nodes are read and rewritten under the writer lock (``write_locked``),
    as a merge group is, so each rewrite builds on the node as it is now.
    """
    if not removed_by_node:
        return
    done = ResolutionReport()
    changed: list[Node] = []
    with repo.write_locked():
        for node_id, removed in removed_by_node.items():
            _drop_aliases(repo, node_id, removed, done, changed.append)
    _committed(report, done, changed, reembed)


def _drop_aliases(
    repo: GraphRepositoryProtocol,
    node_id: str,
    removed: set[str],
    report: ResolutionReport,
    reembed: Callable[[Node], None] | None,
) -> None:
    """Remove the folded aliases in ``removed`` from a node, re-embedding it when they change."""
    node = repo.get_node(node_id, include_embedding=False)
    if node is None:
        return
    props = dict(node.properties or {})
    before = [str(a) for a in props.get("aliases") or []]
    after = [a for a in before if fold(a) not in removed]
    if after == before:
        return
    props["aliases"] = after
    if isinstance(props.get(GIVEN_ALIASES_KEY), list):
        props[GIVEN_ALIASES_KEY] = [
            a for a in props[GIVEN_ALIASES_KEY] if fold(str(a)) not in removed
        ]
    updated = repo.update_node(node.id, NodeUpdate(properties=props))
    report.aliases_dropped.extend((node.label, a) for a in before if fold(a) in removed)
    report.changed_node_ids.add(node.id)
    if reembed is not None and updated is not None:
        reembed(updated)  # the node as it reads now, not before the drop


def _apply_flags(
    repo: GraphRepositoryProtocol, plan: ResolutionPlan, report: ResolutionReport
) -> None:
    """Write each flag's reason onto its edge; an edge already carrying it is left alone.

    Read and written under the writer lock (``write_locked``), as a merge is.
    """
    if not plan.flags:
        return
    flagged = 0
    with repo.write_locked():
        for flag in plan.flags:
            edge = repo.get_edge(flag.edge)
            if edge is None:
                continue
            props = dict(edge.properties or {})
            if props.get(FLAG_KEY) == flag.why:
                continue
            props[FLAG_KEY] = flag.why
            repo.update_edge(edge.id, EdgeUpdate(properties=props))
            flagged += 1
    report.flagged += flagged


def apply_resolution(
    repo: GraphRepositoryProtocol,
    plan: ResolutionPlan,
    *,
    reembed: Callable[[Node], None] | None = None,
    search: SearchRepositoryProtocol | None = None,
    report: ResolutionReport | None = None,
) -> ResolutionReport:
    """Apply ``plan`` to the graph behind ``repo``; see the module docstring.

    ``reembed`` is called with each node whose text changed (a kept node
    after its merge, a node that lost an alias), once the unit of work that
    changed it has committed, for a caller whose index must follow (the
    post-commit pass); a pack re-indexes anyway. ``search``, when given,
    follows the nodes the resolution changed and nothing else
    (:func:`_follow_search`).

    Each merge group, the alias drops and the flags commit as units of their
    own, so a failure partway leaves the earlier units applied. ``report``,
    when the caller passes one, is filled as each unit commits: after an
    exception it names exactly what is in the graph, and the search index
    has followed those nodes before the exception propagates (#795
    follow-up: until then a failure left the merged-away nodes of the
    committed groups in the index). A unit commits only as the outermost
    transaction (``write_locked``): called inside a caller's transaction,
    "committed" means "applied in it", and the search step would follow
    writes the caller may still roll back. Every caller runs it after its
    own commit.
    """
    report = ResolutionReport() if report is None else report
    removed_by_node: dict[str, set[str]] = {}
    for d in plan.alias_drops:
        removed_by_node.setdefault(d.node, set()).add(fold(d.alias))
    try:
        _apply_merges(repo, plan, removed_by_node, report, reembed)  # takes its nodes' drops
        _apply_alias_drops(repo, removed_by_node, report, reembed)
        _apply_flags(repo, plan, report)
    finally:
        if search is not None:
            _follow_search(search, report)
    return report


def _follow_search(search: SearchRepositoryProtocol, report: ResolutionReport) -> None:
    """Bring the search index in step with the nodes ``report`` names, and only those.

    The merged-away nodes lose their keyword and vector rows
    (``delete_nodes_batch``); every surviving node whose text changed has its
    full-text row rewritten from the graph (``refresh_node_fulltext``): a
    merged-away row ranked first for its questions and a survivor kept its
    pre-merge aliases until they did (audit W2-11). Its vector follows through
    the caller's ``reembed``. Until the #795 follow-up this was a rebuild of
    the whole full-text table from a whole-graph read, under the writer lock,
    after any merge: on the source-delete path too.
    """
    if report.removed_node_ids:
        search.delete_nodes_batch(sorted(report.removed_node_ids))
    if report.changed_node_ids - report.removed_node_ids:
        search.refresh_node_fulltext(sorted(report.changed_node_ids - report.removed_node_ids))


def resolve_graph(
    repo: GraphRepositoryProtocol,
    *,
    conventions: NameConventions | None = None,
    titles: Iterable[str] | None = None,
    reembed: Callable[[Node], None] | None = None,
    search: SearchRepositoryProtocol | None = None,
    scope: Iterable[str] | None = None,
    report: ResolutionReport | None = None,
) -> tuple[ResolutionPlan, ResolutionReport]:
    """Plan over the whole graph and apply it: the one call the pack tool and the post-commit pass make.

    ``conventions`` are the graph's domain's naming tables, the ones its
    extraction's deduplication used (``domain_resolver.domain_name_conventions``);
    ``titles`` alone is the convenience form (those title words, every other
    table universal). One of the two is required, so no caller resolves
    under a different set than it extracted by default.
    ``search``, when given, follows the nodes the resolution changed (:func:`apply_resolution`).
    ``scope``, when given, keeps the plan to those node ids and every node
    sharing evidence with them (``plan_resolution(..., scope=)``: the
    post-commit pass passes the nodes the commit wrote).
    ``report``, when given, is filled as each unit of work commits
    (:func:`apply_resolution`): what a caller reads after an exception.

    Raises:
        ValidationError: When neither ``conventions`` nor ``titles`` is given.
    """
    if conventions is None and titles is None:
        msg = "resolve_graph needs the graph's domain conventions (or its title words)"
        raise ValidationError(msg)
    nodes, edges = load_graph(repo)
    plan = plan_resolution(nodes, edges, conventions=conventions, titles=titles, scope=scope)
    return plan, apply_resolution(repo, plan, reembed=reembed, search=search, report=report)


__all__ = [
    "ABSORBED_KEY",
    "FLAG_KEY",
    "RESOLVED_FROM",
    "ResolutionReport",
    "apply_resolution",
    "distinct_readings",
    "edge_create_snapshot",
    "edge_record",
    "fold_members",
    "load_graph",
    "member_readings",
    "node_create_snapshot",
    "node_record",
    "resolve_graph",
    "snapshot_reading",
]
