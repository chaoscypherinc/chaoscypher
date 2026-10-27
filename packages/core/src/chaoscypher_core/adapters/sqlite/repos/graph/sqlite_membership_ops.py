# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Membership operations mixin for GraphRepository.

``graph_nodes.source_id`` names a node's home source. The membership tables
(``graph_node_sources`` / ``graph_edge_sources``) name every source that
attests it, each with the snapshot that source wrote (spec 2026-10-17, D1/D2).

A merge absorbs the dropped node (or edge) into the kept one here too: its
memberships move over (D2) and the citations naming it are repointed (D4).
Citation rows belong to ``SourceCitationsMixin`` on the sources adapter, but
the repoint is part of the merge: resolution holds only the graph port, and the
move must land in the merge's own transaction, so the graph repository writes
those two columns itself.

A source delete, re-commit, force re-extract or reset releases the source here
(D5): its memberships go, and only the rows no source attests any more are
deleted. This is the one place graph rows are deleted by ``source_id`` (a guard
in ``test_ci_config_guards.py`` holds every other module to it).
"""

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy import case, delete, func, or_, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import col, select

from chaoscypher_core.adapters.sqlite.models import (
    GraphEdge,
    GraphEdgeSource,
    GraphNode,
    GraphNodeSource,
    GraphTemplate,
    RelationshipCitation,
    SourceCitation,
    SourceRow,
)
from chaoscypher_core.adapters.sqlite.repos.graph.graph_mixin_base import GraphMixinBase
from chaoscypher_core.adapters.sqlite.utils import SQL_IN_BATCH, id_batches, json_property_path
from chaoscypher_core.models import Edge, EdgeCreate, Node, NodeCreate, ReleasedRows
from chaoscypher_core.services.graph.resolution.apply import (
    ABSORBED_KEY,
    edge_create_snapshot,
    edge_record,
    node_create_snapshot,
    node_record,
    snapshot_reading,
)


#: Rows per INSERT: three bound parameters each, inside the limit of any SQLite build.
_ROWS_PER_INSERT = SQL_IN_BATCH // 3

# ABSORBED_KEY (apply.py) is the snapshot key for a source's further readings (D2).
# A reading equal to one the kept row already holds, its own or an absorbed one
# (:func:`same_reading`), is not appended: it adds nothing a rebuild could use.
# That is the moved copy of an edge, which ``_move_edges`` creates under its
# original's home source and then absorbs the original into.

#: Snapshot fields that say where a reading sits, not what the source wrote.
_PLACEMENT_KEYS = frozenset({"id", "source", "target"})


def _without_absorbed(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The snapshot without the readings it absorbed from same-source merges."""
    return {k: v for k, v in snapshot.items() if k != ABSORBED_KEY}


def _reading(snapshot: dict[str, Any]) -> dict[str, Any]:
    """What the source wrote in a snapshot, with its placement keys set aside."""
    return {k: v for k, v in snapshot_reading(snapshot).items() if k not in _PLACEMENT_KEYS}


def same_reading(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two snapshots say the same thing: equal once placement is set aside.

    Placement is the row id, an edge's endpoints (``source``/``target``), the
    ``absorbed`` list and ``properties.resolved_from`` (which a merge writes).
    Label, type, aliases and every other property must match exactly.
    """
    return _reading(a) == _reading(b)


def node_snapshot(row: GraphNode) -> dict[str, Any]:
    """What a source wrote for this node row: the planner's record, no embedding."""
    return node_record(
        Node(
            id=row.id,
            template_id=row.template_id,
            label=row.label,
            entity_type=row.entity_type,
            properties=row.properties or {},
            source_id=row.source_id,
        )
    )


def edge_snapshot(row: GraphEdge) -> dict[str, Any]:
    """What a source wrote for this edge row: the planner's record."""
    return edge_record(
        Edge(
            id=row.id,
            template_id=row.template_id,
            source_node_id=row.source_node_id,
            target_node_id=row.target_node_id,
            label=row.label,
            properties=row.properties or {},
        )
    )


class MembershipOperationsMixin(GraphMixinBase):
    """Mixin recording and reading which sources attest each node and edge."""

    def record_node_sources(self, rows: Sequence[tuple[str, str, dict[str, Any]]]) -> None:
        """Record ``(node_id, source_id, snapshot)`` memberships.

        A source writing a node again replaces its own snapshot and keeps the
        readings it absorbed in merges (``ABSORBED_KEY``). Joins the caller's
        transaction: nothing is committed here.
        """
        self._record(GraphNodeSource, "node_id", rows)

    def record_edge_sources(self, rows: Sequence[tuple[str, str, dict[str, Any]]]) -> None:
        """Record ``(edge_id, source_id, snapshot)`` memberships; same rules as nodes."""
        self._record(GraphEdgeSource, "edge_id", rows)

    def _record(
        self,
        table: type[GraphNodeSource] | type[GraphEdgeSource],
        owner: str,
        rows: Sequence[tuple[str, str, dict[str, Any]]],
    ) -> None:
        """Upsert memberships in slices that stay inside SQLite's bound-parameter limit.

        On conflict the new snapshot replaces the old one, except that the old
        row's ``ABSORBED_KEY`` list is carried onto it: a refresh rewrites what
        the source wrote, never what it absorbed.
        """
        if not rows:
            return
        self.session.flush()  # the owner rows must exist before their memberships
        path = json_property_path(ABSORBED_KEY)
        current = table.__table__.c.snapshot  # type: ignore[attr-defined]
        for start in range(0, len(rows), _ROWS_PER_INSERT):
            insert = sqlite_insert(table).values(
                [
                    {owner: o, "source_id": s, "snapshot": snap}
                    for o, s, snap in rows[start : start + _ROWS_PER_INSERT]
                ]
            )
            refreshed = case(
                (
                    func.json_type(current, path) == "array",
                    func.json_set(
                        insert.excluded.snapshot, path, func.json(func.json_extract(current, path))
                    ),
                ),
                else_=insert.excluded.snapshot,
            )
            self.session.execute(
                insert.on_conflict_do_update(
                    index_elements=[owner, "source_id"],
                    set_={"snapshot": refreshed},
                )
            )

    def _reattest(
        self,
        table: type[GraphNodeSource] | type[GraphEdgeSource],
        owner: str,
        rows: Sequence[tuple[str, str, dict[str, Any]]],
    ) -> set[str]:
        """Record the ``(owner_id, source_id, snapshot)`` rows whose source holds no membership yet.

        The commit's upsert reuses a row by its stable id ("first write wins"). A
        row the incoming source already attests is its own resume and stays as
        it is. A row it does not attest is one that outlived this source's
        release under another member (a merge's kept side, re-homed), which the
        source now writes again: it gets its membership back, with the reading
        it writes now, so a later release of the other member keeps it (#795).
        Returns those rows' ids, for the rebuild that folds the reading in.
        """
        if not rows:
            return set()
        owner_col = getattr(table, owner)
        have: set[tuple[str, str]] = set()
        for batch in id_batches(sorted({o for o, _, _ in rows})):
            have.update(
                (str(o), str(s))
                for o, s in self.session.execute(
                    select(owner_col, table.source_id).where(col(owner_col).in_(batch or []))
                ).all()
            )
        missing: dict[tuple[str, str], dict[str, Any]] = {}
        for o, sid, snap in rows:
            if (o, sid) not in have:
                missing.setdefault((o, sid), snap)
        self._record(table, owner, [(o, sid, snap) for (o, sid), snap in missing.items()])
        return {o for o, _ in missing}

    def reattest_nodes(self, rows: Sequence[tuple[str, NodeCreate]]) -> set[str]:
        """Give each reused ``(node_id, create)`` its source's membership when it has none.

        See :meth:`_reattest`; the snapshot is :func:`node_create_snapshot`.
        Returns the ids that gained a membership. Joins the caller's transaction.
        """
        return self._reattest(
            GraphNodeSource,
            "node_id",
            [(i, c.source_id, node_create_snapshot(i, c)) for i, c in rows if c.source_id],
        )

    def reattest_edges(self, rows: Sequence[tuple[str, EdgeCreate]]) -> set[str]:
        """:meth:`reattest_nodes` for reused edges (:func:`edge_create_snapshot`)."""
        return self._reattest(
            GraphEdgeSource,
            "edge_id",
            [(i, c.source_id, edge_create_snapshot(i, c)) for i, c in rows if c.source_id],
        )

    def member_source_ids(self) -> set[str]:
        """Every source a node membership names: the graph's sources, read off the membership index."""
        return {
            str(s)
            for s in self.session.exec(select(GraphNodeSource.source_id).distinct()).all()
            if s
        }

    def member_node_ids(self, source_id: str) -> list[str]:
        """The nodes ``source_id`` attests, in membership order."""
        return [
            str(i)
            for i in self.session.exec(
                select(GraphNodeSource.node_id).where(GraphNodeSource.source_id == source_id)
            ).all()
        ]

    def nodes_attested_by(self, source_ids: Collection[str], limit: int) -> list[str]:
        """The nodes ``source_ids`` attest, most memberships among them first, ties by node id.

        At most ``limit``. The ids go to SQL in slices of ``SQL_IN_BATCH``, one grouped
        count per slice, summed here, so a large set never passes SQLite's variable limit;
        a set that fits one slice is ranked and cut in SQL.
        """
        if limit <= 0:
            return []
        batches = [b for b in id_batches(list(source_ids)) if b]
        count = func.count(col(GraphNodeSource.source_id))
        totals: Counter[str] = Counter()
        for batch in batches:
            statement = (
                select(GraphNodeSource.node_id, count)
                .where(col(GraphNodeSource.source_id).in_(batch))
                .group_by(col(GraphNodeSource.node_id))
            )
            if len(batches) == 1:
                statement = statement.order_by(count.desc(), col(GraphNodeSource.node_id)).limit(
                    limit
                )
            for node_id, n in self.session.exec(statement).all():
                totals[str(node_id)] += int(n)
        ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
        return [node_id for node_id, _ in ranked[:limit]]

    def member_edge_ids(self, source_id: str) -> list[str]:
        """The edges ``source_id`` attests, in membership order."""
        return [
            str(i)
            for i in self.session.exec(
                select(GraphEdgeSource.edge_id).where(GraphEdgeSource.source_id == source_id)
            ).all()
        ]

    def memory_source_fields(self, source_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Each source's ``source_type``, ``memory_by``, ``valid_from`` (a date or None), ``created_at``."""
        ids = [str(s) for s in dict.fromkeys(source_ids) if s]
        out: dict[str, dict[str, Any]] = {}
        for batch in id_batches(ids):
            rows = self.session.exec(
                select(
                    SourceRow.id,
                    SourceRow.source_type,
                    SourceRow.memory_by,
                    SourceRow.valid_from,
                    SourceRow.created_at,
                ).where(col(SourceRow.id).in_(batch or []))
            ).all()
            for sid, source_type, by, valid_from, created_at in rows:
                out[str(sid)] = {
                    "source_type": source_type,
                    "memory_by": by,
                    "valid_from": date.fromisoformat(valid_from) if valid_from else None,
                    "created_at": created_at,
                }
        return out

    def source_valid_from(self, source_ids: Iterable[str]) -> dict[str, date | None]:
        """Each source's ``valid_from`` (a non-memory source maps to None)."""
        fields = self.memory_source_fields(source_ids)
        return {sid: fields.get(sid, {}).get("valid_from") for sid in dict.fromkeys(source_ids)}

    def own_reading(self, node: GraphNode) -> dict[str, Any]:
        """The node's own pre-merge reading: its home source's snapshot, else the row.

        What a link with no reading of its own records (``assign_source_to_nodes``):
        the home's snapshot is what the node read as before any merge folded
        other sources in; a row with no membership yet has never been merged.
        """
        home = self.node_members(node.id).get(node.source_id or "")
        return snapshot_reading(home) if home else node_snapshot(node)

    def attest_nodes(self, nodes: Iterable[GraphNode]) -> None:
        """Record each node's home-source membership, snapshotting what it holds now."""
        self.record_node_sources(
            [(n.id, n.source_id, node_snapshot(n)) for n in nodes if n.source_id]
        )

    def attest_edges(self, edges: Iterable[GraphEdge]) -> None:
        """Record each edge's home-source membership, snapshotting what it holds now."""
        self.record_edge_sources(
            [(e.id, e.source_id, edge_snapshot(e)) for e in edges if e.source_id]
        )

    def absorb_node(self, keep_id: str, drop_id: str) -> None:
        """Fold ``drop_id``'s memberships and entity citations into ``keep_id``.

        Call before the dropped node is deleted (its memberships cascade with it).
        Joins the caller's transaction: nothing is committed here.
        """
        self._absorb(GraphNodeSource, "node_id", keep_id, drop_id)
        self.session.execute(
            update(SourceCitation)
            .where(col(SourceCitation.entity_uri) == drop_id)
            .values(entity_uri=keep_id)
            .execution_options(synchronize_session=False)
        )

    def absorb_edge(self, keep_id: str, drop_id: str) -> None:
        """Fold ``drop_id``'s memberships and relationship citations into ``keep_id``.

        Same rules and transaction as :meth:`absorb_node`.
        """
        self._absorb(GraphEdgeSource, "edge_id", keep_id, drop_id)
        self.session.execute(
            update(RelationshipCitation)
            .where(col(RelationshipCitation.edge_id) == drop_id)
            .values(edge_id=keep_id)
            .execution_options(synchronize_session=False)
        )

    def drop_edge_citations(self, edge_id: str) -> None:
        """Delete the relationship citations of an edge whose fact is gone (a merged self-loop)."""
        self.session.execute(
            delete(RelationshipCitation)
            .where(col(RelationshipCitation.edge_id) == edge_id)
            .execution_options(synchronize_session=False)
        )

    def _absorb(
        self,
        table: type[GraphNodeSource] | type[GraphEdgeSource],
        owner: str,
        keep_id: str,
        drop_id: str,
    ) -> None:
        """Move ``drop_id``'s membership rows to ``keep_id``; a shared source folds per D2."""
        if keep_id == drop_id:
            return
        self.session.flush()  # rows this transaction wrote must be visible to the read
        owner_col = getattr(table, owner)
        rows = self.session.execute(
            select(owner_col, table.source_id, table.snapshot).where(
                col(owner_col).in_([keep_id, drop_id])
            )
        ).all()
        kept = {sid: dict(snap) for o, sid, snap in rows if o == keep_id}
        for o, sid, snap in rows:
            if o != drop_id:
                continue
            row = (col(owner_col) == drop_id) & (col(table.source_id) == sid)
            if sid not in kept:
                self.session.execute(
                    update(table)
                    .where(row)
                    .values({owner: keep_id})
                    .execution_options(synchronize_session=False)
                )
                continue
            dropped = dict(snap)
            merged = kept[sid]
            readings = [merged, *merged.get(ABSORBED_KEY, [])]
            added = []
            for reading in (_without_absorbed(dropped), *dropped.get(ABSORBED_KEY, [])):
                if not any(same_reading(reading, have) for have in readings):
                    readings.append(reading)
                    added.append(reading)
            if added:
                merged[ABSORBED_KEY] = [*merged.get(ABSORBED_KEY, []), *added]
                self.session.execute(
                    update(table)
                    .where((col(owner_col) == keep_id) & (col(table.source_id) == sid))
                    .values(snapshot=merged)
                    .execution_options(synchronize_session=False)
                )
            self.session.execute(
                delete(table).where(row).execution_options(synchronize_session=False)
            )

    def _members(
        self, table: type[GraphNodeSource] | type[GraphEdgeSource], owner: str, owner_id: str
    ) -> dict[str, dict[str, Any]]:
        """Columns, not entities: ``_absorb`` writes past the identity map, never stale here."""
        rows = self.session.execute(
            select(table.source_id, table.snapshot).where(col(getattr(table, owner)) == owner_id)
        ).all()
        return {sid: dict(snap) for sid, snap in rows}

    def node_members(self, node_id: str) -> dict[str, dict[str, Any]]:
        """Map source_id to the snapshot that source wrote for this node."""
        return self._members(GraphNodeSource, "node_id", node_id)

    def edge_members(self, edge_id: str) -> dict[str, dict[str, Any]]:
        """Map source_id to the snapshot that source wrote for this edge."""
        return self._members(GraphEdgeSource, "edge_id", edge_id)

    def node_member_sources(self, node_ids: Iterable[str]) -> dict[str, set[str]]:
        """Map each of ``node_ids`` that has members to its member source ids, in batched reads."""
        out: dict[str, set[str]] = {}
        for batch in id_batches(sorted(set(node_ids))):
            rows = self.session.execute(
                select(GraphNodeSource.node_id, GraphNodeSource.source_id).where(
                    col(GraphNodeSource.node_id).in_(batch or [])
                )
            ).all()
            for node_id, source_id in rows:
                out.setdefault(node_id, set()).add(source_id)
        return out

    def rehome(
        self,
        nodes: Mapping[str, str],
        edges: Mapping[str, str],
        *,
        entity_types: Mapping[str, str] | None = None,
    ) -> None:
        """Re-home each node (edge) id to the source it maps to.

        The rebuild after a release re-homes a survivor to a member that still
        attests it (D1): ``source_id`` becomes that source, a node's
        ``entity_type`` the one that source wrote (``entity_types``), and the
        row moves to that source's template of the same type and name when the
        source has one (per-source templates share names); otherwise it keeps
        its template. Joins the caller's transaction: nothing is committed here.
        """
        types = entity_types or {}
        for model, homes in ((GraphNode, nodes), (GraphEdge, edges)):
            if not homes:
                continue
            for batch in id_batches(sorted(homes)):
                rows = self.session.exec(
                    select(model).where(
                        model.database_name == self.database_name,
                        col(model.id).in_(batch or []),
                    )
                ).all()
                for row in rows:
                    home = homes[row.id]
                    row.source_id = home
                    if isinstance(row, GraphNode) and types.get(row.id):
                        row.entity_type = types[row.id]
                    twin = self._template_of(home, row.template_id)
                    if twin is not None:
                        row.template_id = twin
                    self.session.add(row)
        self.session.flush()

    def _template_of(self, source_id: str, template_id: str | None) -> str | None:
        """``source_id``'s template of the same type and name as ``template_id``, if it has one."""
        if template_id is None:
            return None
        current = self.session.get(GraphTemplate, template_id)
        if current is None or current.source_id == source_id:
            return None
        twin = self._twin_template(current, source_id)
        return None if twin is None else twin.id

    def _twin_template(self, template: GraphTemplate, owner: str | None) -> GraphTemplate | None:
        """``owner``'s template of the same type and name as ``template`` (``owner`` None: an unowned one).

        The one lookup the re-home (:meth:`_template_of`) and the template
        release (:meth:`_release_templates`) share. A system template is never
        a twin: it is unowned too (``source_id`` NULL), so until the #795
        follow-up a released user template whose users had no membership could
        fold into the system template of the same name and lose its own
        description and properties.
        """
        return self.session.exec(
            select(GraphTemplate).where(
                GraphTemplate.database_name == self.database_name,
                GraphTemplate.source_id == owner
                if owner is not None
                else col(GraphTemplate.source_id).is_(None),
                GraphTemplate.template_type == template.template_type,
                GraphTemplate.name == template.name,
                GraphTemplate.id != template.id,
                col(GraphTemplate.is_system).is_(False),
            )
        ).first()

    # ---- Release (D5) ----

    def release_source(self, source_id: str) -> ReleasedRows:
        """Take ``source_id`` out of the graph: its memberships, and what only it attested.

        Candidates are the nodes and edges the source is a member of, plus the
        rows homed at it (``source_id``) that carry no membership row of their
        own (written before memberships existed). Every candidate loses the
        source's membership. A candidate with no membership left is deleted (an
        edge also goes with a deleted endpoint, and is reported with the deleted
        edges), unless a person made it (``person_made``): a row a person made is
        never deleted by a release. The other candidates survive, untouched, in
        ``touched_*``, whether or not the source still attested them. The source's templates follow D6
        (:meth:`_release_templates`). Citations are the caller's: the source
        delete and the re-commit remove the source's own.

        Joins the caller's transaction (flushes inside ``adapter.transaction()``,
        commits otherwise). Never deletes the source row.
        """
        return self._release([source_id])

    def release_all_sources(self) -> None:
        """Release every source of this database at once (the reset paths delete them all).

        One pass, so the outcome does not depend on the order sources are released in.
        """
        source_ids = self.session.exec(
            select(SourceRow.id).where(SourceRow.database_name == self.database_name)
        ).all()
        if source_ids:
            self._release(list(source_ids))

    def _release(self, source_ids: list[str]) -> ReleasedRows:
        """Release ``source_ids`` together: memberships, dead rows once, then D6.

        First, what the sources closed is reopened (a forgotten memory takes its
        closures with it, spec 2026-10-20 decision 7): every release path.
        A person-made row (``person_made``, migration 0025) loses the sources'
        memberships and stays, as it was written, on every path, the source and
        data resets (:meth:`release_all_sources`) included; only ``clear_all`` (the
        knowledge-graph reset) deletes it.
        """
        session = self.session
        session.flush()
        for source_id in source_ids:
            self.reopen_edges_closed_by(source_id)
        node_ids = self._released(GraphNodeSource, "node_id", GraphNode, source_ids)
        edge_ids = self._released(GraphEdgeSource, "edge_id", GraphEdge, source_ids)
        dead_nodes = node_ids - self._attested(GraphNodeSource, "node_id", node_ids)
        dead_edges = edge_ids - self._attested(GraphEdgeSource, "edge_id", edge_ids)
        # a row a person made is never released with a source that attested it: it
        # loses that membership and stays, as it was written
        dead_nodes -= self._person_made(GraphNode, dead_nodes)
        dead_edges -= self._person_made(GraphEdge, dead_edges)
        for batch in id_batches(sorted(dead_nodes)):  # their edges cascade: report them too
            dead_edges.update(
                session.exec(
                    select(GraphEdge.id).where(
                        or_(
                            col(GraphEdge.source_node_id).in_(batch or []),
                            col(GraphEdge.target_node_id).in_(batch or []),
                        )
                    )
                ).all()
            )
        for batch in id_batches(sorted(dead_edges)):
            session.exec(delete(GraphEdge).where(col(GraphEdge.id).in_(batch or [])))
        for batch in id_batches(sorted(dead_nodes)):
            session.exec(delete(GraphNode).where(col(GraphNode.id).in_(batch or [])))
        self._release_templates(source_ids)
        session.maybe_commit()
        return ReleasedRows(
            deleted_node_ids=frozenset(dead_nodes),
            deleted_edge_ids=frozenset(dead_edges),
            touched_node_ids=frozenset(node_ids - dead_nodes),
            touched_edge_ids=frozenset(edge_ids - dead_edges),
        )

    def _released(
        self,
        table: type[GraphNodeSource] | type[GraphEdgeSource],
        owner: str,
        model: type[GraphNode] | type[GraphEdge],
        source_ids: list[str],
    ) -> set[str]:
        """Delete the sources' memberships; return their owners plus the rows homed at them.

        A row homed at a released source with no membership row (written before
        memberships existed) is released too, as the cascade used to take it.
        """
        owner_col = getattr(table, owner)
        ids: set[str] = set()
        for batch in id_batches(source_ids):
            released = col(table.source_id).in_(batch or [])
            ids.update(self.session.exec(select(owner_col).where(released)).all())
            ids.update(
                self.session.exec(
                    select(model.id).where(
                        model.database_name == self.database_name,
                        col(model.source_id).in_(batch or []),
                    )
                ).all()
            )
            self.session.exec(
                delete(table).where(released).execution_options(synchronize_session=False)
            )
        return ids

    def _attested(
        self,
        table: type[GraphNodeSource] | type[GraphEdgeSource],
        owner: str,
        ids: set[str],
    ) -> set[str]:
        """Those of ``ids`` some source is still a member of."""
        owner_col = getattr(table, owner)
        still: set[str] = set()
        for batch in id_batches(sorted(ids)):
            still.update(
                self.session.exec(
                    select(owner_col).where(col(owner_col).in_(batch or [])).distinct()
                ).all()
            )
        return still

    def _person_made(self, model: type[GraphNode] | type[GraphEdge], ids: set[str]) -> set[str]:
        """Those of ``ids`` a person made (``person_made``, migration 0025)."""
        marked: set[str] = set()
        for batch in id_batches(sorted(ids)):
            marked.update(
                self.session.exec(
                    select(model.id).where(
                        col(model.id).in_(batch or []), col(model.person_made).is_(True)
                    )
                ).all()
            )
        return marked

    def _release_templates(self, source_ids: list[str]) -> None:
        """D6 (revised in review): a released source's template goes with its last user.

        A template nothing uses any more is deleted. One still used is re-homed to
        the lowest source id that is a member of one of its users, so the release
        of that source applies D6 to it in turn and no template outlives every
        member. Only a template whose users carry no membership at all (legacy,
        unowned rows) is left with ``source_id`` NULL. Either way, when the new
        owner (a source, or none) already holds a template of the same type and
        name (per-source templates share names), the users are repointed to that
        one and the released template is deleted: the unique key lets NULL owners
        repeat, so without it every such release added another unowned copy (#795).
        A system template is never that twin (:meth:`_twin_template`).
        """
        owned = list(
            self.session.exec(
                select(GraphTemplate).where(
                    GraphTemplate.database_name == self.database_name,
                    col(GraphTemplate.source_id).in_(source_ids),
                )
            ).all()
        )
        for template in owned:
            owner = self._template_member_source(template.id)
            if owner is None and not self._template_used(template.id):
                self.session.delete(template)
                continue
            twin = self._twin_template(template, owner)
            if twin is None:
                template.source_id = owner
                self.session.add(template)
                continue
            for model in (GraphNode, GraphEdge):
                self.session.exec(
                    update(model)
                    .where(col(model.template_id) == template.id)
                    .values(template_id=twin.id)
                    .execution_options(synchronize_session="fetch")
                )
            self.session.flush()
            self.session.delete(template)
        self.session.flush()

    def _template_used(self, template_id: str) -> bool:
        """Whether any node or edge still points at the template."""
        return any(
            self.session.exec(
                select(model.id).where(col(model.template_id) == template_id).limit(1)
            ).first()
            is not None
            for model in (GraphNode, GraphEdge)
        )

    def _template_member_source(self, template_id: str) -> str | None:
        """The lowest source id that is a member of a node or edge using ``template_id``."""
        owners = [
            self.session.exec(
                select(func.min(table.source_id))
                .join(model, col(model.id) == col(getattr(table, owner)))
                .where(col(model.template_id) == template_id)
            ).one()
            for table, owner, model in (
                (GraphNodeSource, "node_id", GraphNode),
                (GraphEdgeSource, "edge_id", GraphEdge),
            )
        ]
        present = [o for o in owners if o is not None]
        return min(present) if present else None
