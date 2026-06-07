# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SQL Node Repository.

Data access layer for node-related SQL queries (citations, sources, chunks).
"""

import structlog
from sqlalchemy import literal, union_all
from sqlalchemy.orm import load_only
from sqlmodel import Session, func, select

from chaoscypher_core.adapters.sqlite.models import (
    DocumentChunk,
    GraphEdge,
    SourceCitation,
    SourceRow,
)


logger = structlog.get_logger(__name__)


class SqlNodeRepository:
    """Repository for node-related SQL queries.

    Handles SQLModel-based queries for citations, sources, and chunks.
    Session is injected at construction time per CLAUDE.md Pattern 3.
    """

    def __init__(
        self,
        session: Session,
        database_name: str,
        max_connected_edges: int = 2000,
    ):
        """Initialize SQL node repository.

        Args:
            session: SQLModel session for database queries
            database_name: Current database name for filtering
            max_connected_edges: Safety cap for connected-nodes edge query

        """
        self.session = session
        self.database_name = database_name
        self._max_connected_edges = max_connected_edges

    def get_citations_for_node(
        self,
        node_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[tuple[SourceCitation, SourceRow, DocumentChunk]], int]:
        """Get citations for a node with pagination.

        Args:
            node_id: Node ID (entity URI in RDF graph)
            offset: Pagination offset
            limit: Maximum results

        Returns:
            Tuple of (list of (SourceCitation, SourceRow, DocumentChunk) tuples, total count)

        """
        # Query citations with joins (exclude large BLOB columns from DocumentChunk)
        query = (
            select(SourceCitation, SourceRow, DocumentChunk)
            .join(SourceRow, SourceCitation.source_id == SourceRow.id)
            .join(DocumentChunk, SourceCitation.chunk_id == DocumentChunk.id)
            .options(
                load_only(
                    DocumentChunk.id,
                    DocumentChunk.database_name,
                    DocumentChunk.source_id,
                    DocumentChunk.chunk_index,
                    DocumentChunk.content,
                    DocumentChunk.embedding_model,
                    DocumentChunk.embedding_dimensions,
                    DocumentChunk.page_number,
                    DocumentChunk.section,
                    DocumentChunk.chunk_metadata,
                    DocumentChunk.status,
                    DocumentChunk.created_at,
                )
            )
            .where(
                SourceCitation.entity_uri == node_id,
                SourceCitation.database_name == self.database_name,
            )
            .order_by(SourceCitation.created_at.desc())  # type: ignore[attr-defined]
            .offset(offset)
            .limit(limit)
        )
        results = list(self.session.exec(query))

        # Count total citations using func.count() for efficiency
        count_query = (
            select(func.count())
            .select_from(SourceCitation)
            .where(
                SourceCitation.entity_uri == node_id,
                SourceCitation.database_name == self.database_name,
            )
        )
        total = self.session.exec(count_query).one()

        return results, total

    def get_source_id_for_node(
        self, node_id: str, node_label: str, node_definition: str | None
    ) -> str | None:
        """Check if a node is a source document and return its source_id.

        Strategy: Query SourceRow table for matching source nodes.
        Source nodes are created with node IDs that can be traced back.

        Args:
            node_id: Node ID to check
            node_label: Node label for matching
            node_definition: Node definition property for source detection

        Returns:
            source_id if found, None otherwise

        """
        try:
            # Check if this is a source document node
            if not node_definition or not node_definition.startswith("Source document:"):
                return None

            # Use SQL instr() to find sources whose title appears in the node label,
            # avoiding loading all sources into Python for substring matching.
            query = (
                select(SourceRow.id)
                .where(
                    SourceRow.database_name == self.database_name,
                    SourceRow.title != None,  # noqa: E711
                    func.instr(literal(node_label), SourceRow.title) > 0,
                )
                .limit(1)
            )
            return self.session.exec(query).first()
        except Exception as e:
            logger.warning(
                "source_id_lookup_failed",
                node_id=node_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return None

    def get_node_stats_batch(self, node_ids: list[str]) -> dict[str, dict[str, int]]:
        """Get edge and citation stats for multiple nodes in batch.

        Efficiently computes stats for all nodes in a single set of queries.

        Args:
            node_ids: List of node IDs to get stats for

        Returns:
            Dict mapping node_id to stats dict with keys:
            - incoming_edge_count
            - outgoing_edge_count
            - edge_count (total)
            - citation_count
            - relationship_type_count

        """
        if not node_ids:
            return {}

        # Initialize stats for all nodes
        stats: dict[str, dict[str, int]] = {
            node_id: {
                "incoming_edge_count": 0,
                "outgoing_edge_count": 0,
                "edge_count": 0,
                "citation_count": 0,
                "relationship_type_count": 0,
            }
            for node_id in node_ids
        }

        # Get incoming edge counts (where node is target)
        incoming_query = (
            select(GraphEdge.target_node_id, func.count(GraphEdge.id))
            .where(
                GraphEdge.database_name == self.database_name,
                GraphEdge.target_node_id.in_(node_ids),  # type: ignore[attr-defined]
            )
            .group_by(GraphEdge.target_node_id)
        )
        for node_id, count in self.session.exec(incoming_query).all():
            if node_id in stats:
                stats[node_id]["incoming_edge_count"] = count

        # Get outgoing edge counts (where node is source)
        outgoing_query = (
            select(GraphEdge.source_node_id, func.count(GraphEdge.id))
            .where(
                GraphEdge.database_name == self.database_name,
                GraphEdge.source_node_id.in_(node_ids),  # type: ignore[attr-defined]
            )
            .group_by(GraphEdge.source_node_id)
        )
        for node_id, count in self.session.exec(outgoing_query).all():
            if node_id in stats:
                stats[node_id]["outgoing_edge_count"] = count

        # Calculate total edge counts
        for node_stats in stats.values():
            node_stats["edge_count"] = (
                node_stats["incoming_edge_count"] + node_stats["outgoing_edge_count"]
            )

        # Get citation counts
        citation_query = (
            select(SourceCitation.entity_uri, func.count(SourceCitation.id))
            .where(
                SourceCitation.database_name == self.database_name,
                SourceCitation.entity_uri.in_(node_ids),  # type: ignore[attr-defined]
            )
            .group_by(SourceCitation.entity_uri)
        )
        for node_id, count in self.session.exec(citation_query).all():
            if node_id in stats:
                stats[node_id]["citation_count"] = count

        # Get relationship type counts via single UNION ALL query
        incoming_rels = select(
            GraphEdge.target_node_id.label("node_id"),
            GraphEdge.template_id,
        ).where(
            GraphEdge.database_name == self.database_name,
            GraphEdge.target_node_id.in_(node_ids),  # type: ignore[attr-defined]
        )
        outgoing_rels = select(
            GraphEdge.source_node_id.label("node_id"),
            GraphEdge.template_id,
        ).where(
            GraphEdge.database_name == self.database_name,
            GraphEdge.source_node_id.in_(node_ids),  # type: ignore[attr-defined]
        )
        rel_sub = union_all(incoming_rels, outgoing_rels).subquery()
        rel_type_query = (
            select(
                rel_sub.c.node_id,
                func.count(func.distinct(rel_sub.c.template_id)),
            )
            .where(rel_sub.c.template_id.is_not(None))
            .group_by(rel_sub.c.node_id)
        )
        for node_id, type_count in self.session.exec(rel_type_query).all():
            if node_id in stats:
                stats[node_id]["relationship_type_count"] = type_count

        return stats

    def get_connected_nodes(
        self,
        node_id: str,
        sort_by: str = "edge_count",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[dict], int]:
        """Get nodes connected to a given node with their edge counts.

        Args:
            node_id: Node ID to get connections for
            sort_by: Sort field (edge_count, label, relationship)
            offset: Pagination offset
            limit: Maximum results

        Returns:
            Tuple of (list of connected node dicts, total count)

        """
        from chaoscypher_core.adapters.sqlite.models import GraphNode

        # Get all edges involving this node
        edges_query = (
            select(GraphEdge)
            .options(
                load_only(
                    GraphEdge.id,
                    GraphEdge.database_name,
                    GraphEdge.source_node_id,
                    GraphEdge.target_node_id,
                    GraphEdge.label,
                    GraphEdge.template_id,
                )
            )
            .where(
                GraphEdge.database_name == self.database_name,
                (GraphEdge.source_node_id == node_id) | (GraphEdge.target_node_id == node_id),
            )
            .limit(self._max_connected_edges)
        )
        edges = list(self.session.exec(edges_query))

        # Build connected nodes map
        connected_nodes: dict[str, dict] = {}
        for edge in edges:
            if edge.source_node_id == node_id:
                # Outgoing edge
                connected_id = edge.target_node_id
                direction = "outgoing"
            else:
                # Incoming edge
                connected_id = edge.source_node_id
                direction = "incoming"

            if connected_id not in connected_nodes:
                connected_nodes[connected_id] = {
                    "id": connected_id,
                    "relationship": edge.label or edge.template_id,
                    "direction": direction,
                    "edge_count": 0,
                }
            connected_nodes[connected_id]["edge_count"] += 1

        # Get node details for connected nodes
        if connected_nodes:
            nodes_query = (
                select(GraphNode)
                .options(
                    load_only(
                        GraphNode.id,
                        GraphNode.label,
                        GraphNode.template_id,
                    )
                )
                .where(
                    GraphNode.database_name == self.database_name,
                    GraphNode.id.in_(list(connected_nodes.keys())),  # type: ignore[attr-defined]
                )
            )
            node_details = {n.id: n for n in self.session.exec(nodes_query)}

            # Merge node details
            for conn_id, conn_data in connected_nodes.items():
                if conn_id in node_details:
                    node = node_details[conn_id]
                    conn_data["label"] = node.label
                    conn_data["template_id"] = node.template_id
                else:
                    conn_data["label"] = "Unknown"
                    conn_data["template_id"] = "unknown"

            # Get total edge counts for connected nodes (single UNION ALL query)
            all_conn_ids = list(connected_nodes.keys())
            inc_edges = (
                select(
                    GraphEdge.target_node_id.label("node_id"),
                    func.count(GraphEdge.id).label("cnt"),
                )
                .where(
                    GraphEdge.database_name == self.database_name,
                    GraphEdge.target_node_id.in_(all_conn_ids),  # type: ignore[attr-defined]
                )
                .group_by(GraphEdge.target_node_id)
            )
            out_edges = (
                select(
                    GraphEdge.source_node_id.label("node_id"),
                    func.count(GraphEdge.id).label("cnt"),
                )
                .where(
                    GraphEdge.database_name == self.database_name,
                    GraphEdge.source_node_id.in_(all_conn_ids),  # type: ignore[attr-defined]
                )
                .group_by(GraphEdge.source_node_id)
            )
            edge_sub = union_all(inc_edges, out_edges).subquery()
            total_edges_query = select(
                edge_sub.c.node_id,
                func.sum(edge_sub.c.cnt),
            ).group_by(edge_sub.c.node_id)
            edge_totals = dict(self.session.exec(total_edges_query).all())

            for conn_id, conn_data in connected_nodes.items():
                conn_data["edge_count"] = edge_totals.get(conn_id, 0)

        # Sort results in-memory.  Cardinality is bounded by the direct
        # connections of a single node — typically <500 in knowledge graphs.
        result_list = list(connected_nodes.values())
        if sort_by == "edge_count":
            result_list.sort(key=lambda x: x["edge_count"], reverse=True)
        elif sort_by == "label":
            result_list.sort(key=lambda x: x.get("label", "").lower())
        elif sort_by == "relationship":
            result_list.sort(key=lambda x: x.get("relationship", "").lower())

        total = len(result_list)

        # Apply pagination
        result_list = result_list[offset : offset + limit]

        return result_list, total
