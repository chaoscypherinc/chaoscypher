# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Deterministic graph seed helper for integration tests.

Produces a reproducible two-source, three-template graph suitable for
asserting aggregation logic in BuildGraphSnapshotService.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode, GraphTemplate, SourceRow


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


@dataclass
class SeedGraphResult:
    """Expected counts produced by seed_two_sources_three_templates."""

    total_nodes: int  # 14
    total_edges: int  # 6
    src_a_entities: int  # 8
    src_a_internal_links: int  # 3
    src_b_entities: int  # 6
    src_b_internal_links: int  # 2
    src_a_template_counts: dict[str, int]  # {"tpl_person": 5, "tpl_location": 3}
    src_b_template_counts: dict[str, int]  # {"tpl_person": 2, "tpl_concept": 4}


def seed_two_sources_three_templates(
    adapter: SqliteAdapter,
    database_name: str = "default",
) -> SeedGraphResult:
    """Write a deterministic two-source, three-template graph to the adapter.

    Layout:
    - Source A (src_a, "Paper A", pdf): 5 Person nodes + 3 Location nodes = 8
    - Source B (src_b, "Paper B", pdf): 2 Person nodes + 4 Concept nodes = 6
    - Templates: tpl_person (shared), tpl_location (A only), tpl_concept (B only, color=None)
    - Edges:
      - 3 internal to Source A
      - 2 internal to Source B
      - 1 cross-source (A→B)

    Args:
        adapter: Connected SqliteAdapter whose session will receive the rows.
        database_name: database_name tag to use (default "default").

    Returns:
        SeedGraphResult with expected counts for test assertions.

    """
    assert adapter.session is not None, "adapter must be connected"
    session = adapter.session

    # --- Sources ---
    src_a = SourceRow(
        id="src_a",
        database_name=database_name,
        filename="paper_a.pdf",
        filepath="/data/paper_a.pdf",
        title="Paper A",
        source_type="pdf",
        status="committed",
    )
    src_b = SourceRow(
        id="src_b",
        database_name=database_name,
        filename="paper_b.pdf",
        filepath="/data/paper_b.pdf",
        title="Paper B",
        source_type="pdf",
        status="committed",
    )
    session.add(src_a)
    session.add(src_b)
    session.flush()  # Sources must exist before nodes reference them

    # --- Templates ---
    tpl_person = GraphTemplate(
        id="tpl_person",
        database_name=database_name,
        name="Person",
        template_type="node",
        color="#ff0000",
    )
    tpl_location = GraphTemplate(
        id="tpl_location",
        database_name=database_name,
        name="Location",
        template_type="node",
        color="#00ff00",
    )
    tpl_concept = GraphTemplate(
        id="tpl_concept",
        database_name=database_name,
        name="Concept",
        template_type="node",
        color=None,  # exercises #888888 fallback
    )
    session.add(tpl_person)
    session.add(tpl_location)
    session.add(tpl_concept)
    session.flush()  # Templates must exist before nodes reference them (FK)

    # --- Source A nodes: 5 Person + 3 Location ---
    src_a_person_nodes: list[str] = []
    for i in range(5):
        node_id = f"node_src_a_person_{i}"
        session.add(
            GraphNode(
                id=node_id,
                database_name=database_name,
                graph_name="knowledge",
                template_id="tpl_person",
                label=f"Person A{i}",
                source_id="src_a",
            )
        )
        src_a_person_nodes.append(node_id)

    src_a_location_nodes: list[str] = []
    for i in range(3):
        node_id = f"node_src_a_location_{i}"
        session.add(
            GraphNode(
                id=node_id,
                database_name=database_name,
                graph_name="knowledge",
                template_id="tpl_location",
                label=f"Location A{i}",
                source_id="src_a",
            )
        )
        src_a_location_nodes.append(node_id)

    # --- Source B nodes: 2 Person + 4 Concept ---
    src_b_person_nodes: list[str] = []
    for i in range(2):
        node_id = f"node_src_b_person_{i}"
        session.add(
            GraphNode(
                id=node_id,
                database_name=database_name,
                graph_name="knowledge",
                template_id="tpl_person",
                label=f"Person B{i}",
                source_id="src_b",
            )
        )
        src_b_person_nodes.append(node_id)

    src_b_concept_nodes: list[str] = []
    for i in range(4):
        node_id = f"node_src_b_concept_{i}"
        session.add(
            GraphNode(
                id=node_id,
                database_name=database_name,
                graph_name="knowledge",
                template_id="tpl_concept",
                label=f"Concept B{i}",
                source_id="src_b",
            )
        )
        src_b_concept_nodes.append(node_id)

    # Flush nodes so FK constraints on graph_edges are satisfied
    session.flush()

    # --- Edges ---
    # 3 internal to Source A (person→person, person→location, location→location)
    tpl_edge = GraphTemplate(
        id="tpl_edge_rel",
        database_name=database_name,
        name="Related",
        template_type="edge",
        color=None,
    )
    session.add(tpl_edge)
    session.flush()  # Edge template must exist before edges reference it (FK)

    internal_a_edges = [
        ("edge_a_0", src_a_person_nodes[0], src_a_person_nodes[1]),
        ("edge_a_1", src_a_person_nodes[0], src_a_location_nodes[0]),
        ("edge_a_2", src_a_location_nodes[0], src_a_location_nodes[1]),
    ]
    for edge_id, src_nid, tgt_nid in internal_a_edges:
        session.add(
            GraphEdge(
                id=edge_id,
                database_name=database_name,
                graph_name="knowledge",
                template_id="tpl_edge_rel",
                source_node_id=src_nid,
                target_node_id=tgt_nid,
                label="related_to",
                source_id="src_a",
            )
        )

    # 2 internal to Source B
    internal_b_edges = [
        ("edge_b_0", src_b_person_nodes[0], src_b_concept_nodes[0]),
        ("edge_b_1", src_b_concept_nodes[0], src_b_concept_nodes[1]),
    ]
    for edge_id, src_nid, tgt_nid in internal_b_edges:
        session.add(
            GraphEdge(
                id=edge_id,
                database_name=database_name,
                graph_name="knowledge",
                template_id="tpl_edge_rel",
                source_node_id=src_nid,
                target_node_id=tgt_nid,
                label="related_to",
                source_id="src_b",
            )
        )

    # 1 cross-source edge (A→B)
    session.add(
        GraphEdge(
            id="edge_cross_0",
            database_name=database_name,
            graph_name="knowledge",
            template_id="tpl_edge_rel",
            source_node_id=src_a_person_nodes[2],
            target_node_id=src_b_person_nodes[0],
            label="references",
            source_id="src_a",
        )
    )

    session.commit()

    return SeedGraphResult(
        total_nodes=14,
        total_edges=6,
        src_a_entities=8,
        src_a_internal_links=3,
        src_b_entities=6,
        src_b_internal_links=2,
        src_a_template_counts={"tpl_person": 5, "tpl_location": 3},
        src_b_template_counts={"tpl_person": 2, "tpl_concept": 4},
    )
