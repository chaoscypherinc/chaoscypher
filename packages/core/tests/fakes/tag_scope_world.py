# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A small real-SQLite graph for the tag-scope tests: two tagged documents and four kinds of node.

``doc-a`` carries the tag ``alpha``, ``doc-b`` the tag ``beta``. The nodes,
every one labelled ``Falcon ...`` so one keyword finds them all:

- ``node-a``: attested by ``doc-a`` alone (a membership row);
- ``node-a2``: attested by ``doc-a`` alone too;
- ``node-b``: attested by ``doc-b`` alone;
- ``node-person``: made by a person, no source;
- ``node-orphan``: no member and no source (its source was deleted).

``node-a`` is linked to ``node-person`` and to ``node-b``, so a graph
context that expands to neighbours meets both; and to ``node-a2`` by an
edge only ``doc-b`` states (:data:`UNTAGGED_RELATION`), so both its ends
are in the ``alpha`` scope while the relationship is not.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    GraphEdge,
    GraphEdgeSource,
    GraphNode,
    GraphNodeSource,
    GraphTemplate,
    SourceRow,
)
from chaoscypher_core.adapters.sqlite.safe_session import SafeSession


if TYPE_CHECKING:
    from pathlib import Path


DATABASE = "db"
"""The database name every row is written under."""

DOC_TAGS: dict[str, str] = {"doc-a": "alpha", "doc-b": "beta"}
"""Each document and its one tag."""

NODES: dict[str, dict[str, object]] = {
    "node-a": {"label": "Falcon Aerie", "member": "doc-a", "vector": [1.0, 0.0, 0.0, 0.0]},
    "node-a2": {"label": "Falcon Aspen", "member": "doc-a", "vector": [0.995, 0.05, 0.0, 0.0]},
    "node-b": {"label": "Falcon Bluff", "member": "doc-b", "vector": [0.99, 0.1, 0.0, 0.0]},
    "node-person": {"label": "Falcon Perch", "person": True, "vector": [0.98, 0.15, 0.0, 0.0]},
    "node-orphan": {"label": "Falcon Outpost", "vector": [0.97, 0.2, 0.0, 0.0]},
}
"""The four nodes: their labels, member source, whether a person made them, and their vectors."""

UNTAGGED_RELATION = "rumoured_beside"
"""The label of the one edge only ``doc-b`` states, between two ``doc-a`` nodes."""

SHARED_RELATION = "shares_a_ridge_with"
"""The label of an edge both documents state, homed at ``doc-b``: in the ``alpha`` scope by its members."""

EDGES: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("edge-a-person", "node-a", "node-person", "near", "doc-a"),
    ("edge-a-b", "node-a", "node-b", "near", "doc-a"),
    ("edge-a-a2", "node-a", "node-a2", UNTAGGED_RELATION, "doc-b"),
    ("edge-a2-a", "node-a2", "node-a", SHARED_RELATION, "doc-b"),
)
"""``node-a``'s links: ``(id, from, to, label, its home source)``; each is stated by its home."""

EXTRA_EDGE_MEMBERS: dict[str, tuple[str, ...]] = {"edge-a2-a": ("doc-a",)}
"""Sources stating an edge besides its home (a merged edge)."""

QUERY_VECTOR = [1.0, 0.0, 0.0, 0.0]
"""A query vector nearest ``node-a`` and near every other node."""


def build_world(path: Path) -> None:
    """Write the documents, their tags, the nodes, their memberships and the edges into ``path``."""
    engine = get_engine(str(path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    with SafeSession(engine) as session:
        for sid in DOC_TAGS:
            session.add(
                SourceRow(id=sid, database_name=DATABASE, filename=f"{sid}.txt", filepath=sid)
            )
        for tid, kind in (("place", "node"), ("near", "edge")):
            session.add(
                GraphTemplate(
                    id=tid, database_name=DATABASE, name=tid, description=tid, template_type=kind
                )
            )
        session.commit()
        for nid, spec in NODES.items():
            member = spec.get("member")
            session.add(
                GraphNode(
                    id=nid,
                    database_name=DATABASE,
                    graph_name="knowledge",
                    template_id="place",
                    label=str(spec["label"]),
                    properties={"description": f"{spec['label']} is a place of falcons."},
                    source_id=str(member) if member else None,
                    person_made=bool(spec.get("person", False)),
                )
            )
        session.commit()
        for nid, spec in NODES.items():
            if spec.get("member"):
                session.add(GraphNodeSource(node_id=nid, source_id=str(spec["member"])))
        for eid, start, end, label, stated_by in EDGES:
            session.add(
                GraphEdge(
                    id=eid,
                    database_name=DATABASE,
                    template_id="near",
                    source_node_id=start,
                    target_node_id=end,
                    label=label,
                    source_id=stated_by,
                )
            )
        session.commit()
        for eid, *_, stated_by in EDGES:
            for sid in (stated_by, *EXTRA_EDGE_MEMBERS.get(eid, ())):
                if sid:
                    session.add(GraphEdgeSource(edge_id=eid, source_id=sid))
        session.commit()
    with sqlite3.connect(path) as con:
        for sid, name in DOC_TAGS.items():
            con.execute(
                "INSERT INTO source_tags (id, database_name, name, created_at) "
                "VALUES (?, ?, ?, '2026-11-05')",
                (f"tag-{name}", DATABASE, name),
            )
            con.execute(
                "INSERT INTO source_tag_assignments (id, source_id, tag_id, database_name, "
                "assigned_at) VALUES (?, ?, ?, ?, '2026-11-05')",
                (f"as-{sid}-{name}", sid, f"tag-{name}", DATABASE),
            )


__all__ = [
    "DATABASE",
    "DOC_TAGS",
    "EDGES",
    "NODES",
    "QUERY_VECTOR",
    "SHARED_RELATION",
    "UNTAGGED_RELATION",
    "build_world",
]
