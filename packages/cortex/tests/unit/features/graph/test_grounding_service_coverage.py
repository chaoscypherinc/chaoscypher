# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage-focused unit tests for GroundingService.

Exercises the happy-path query/pagination logic of the MCP grounding
service with a fully mocked GraphRepository and Settings. Complements
``test_grounding_service_exceptions.py`` (which covers the raise paths).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from chaoscypher_core.models import Edge, Node
from chaoscypher_cortex.features.graph.grounding_service import GroundingService


def _make_node(node_id: str, label: str = "Label", **props: object) -> Node:
    """Build a Node with the given id/label and optional properties."""
    return Node(
        id=node_id,
        label=label,
        template_id="tmpl-1",
        properties=dict(props),
    )


def _make_edge(
    edge_id: str,
    source: str,
    target: str,
    label: str = "RELATES_TO",
) -> Edge:
    """Build an Edge between two node ids."""
    return Edge(
        id=edge_id,
        template_id="rel-tmpl",
        source_node_id=source,
        target_node_id=target,
        label=label,
        properties={"weight": 1},
    )


def _make_settings(
    *,
    default_page_size: int = 20,
    max_page_size: int = 100,
    default_list_limit: int = 50,
    edge_list_limit: int = 200,
) -> MagicMock:
    """Build a mock Settings object with the pagination/batching knobs."""
    settings = MagicMock()
    settings.pagination.default_page_size = default_page_size
    settings.pagination.max_page_size = max_page_size
    settings.pagination.default_list_limit = default_list_limit
    settings.batching.edge_list_limit = edge_list_limit
    return settings


def _make_service(
    repo: MagicMock | None = None, settings: MagicMock | None = None
) -> GroundingService:
    """Build a GroundingService with mock repo + settings."""
    repo = repo if repo is not None else MagicMock()
    settings = settings if settings is not None else _make_settings()
    return GroundingService(graph_repository=repo, settings=settings)


# ---------------------------------------------------------------------------
# search_nodes
# ---------------------------------------------------------------------------


def test_search_nodes_no_filter_uses_total_count() -> None:
    """Without template_id the total comes from count_nodes()."""
    repo = MagicMock()
    repo.list_nodes.return_value = [_make_node("n1"), _make_node("n2")]
    repo.count_nodes.return_value = 45
    service = _make_service(repo=repo, settings=_make_settings(default_page_size=20))

    result = service.search_nodes(page=1)

    assert len(result.data) == 2
    assert result.pagination.total == 45
    # 45 / 20 -> 3 pages
    assert result.pagination.total_pages == 3
    assert result.pagination.has_next is True
    assert result.pagination.has_prev is False
    repo.count_nodes.assert_called_once()
    repo.count_nodes_by_template.assert_not_called()
    repo.list_nodes.assert_called_once_with(template_id=None, skip=0, limit=20)


def test_search_nodes_with_template_id_uses_template_count() -> None:
    """template_id routes the total through count_nodes_by_template()."""
    repo = MagicMock()
    repo.list_nodes.return_value = [_make_node("n1")]
    repo.count_nodes_by_template.return_value = 1
    service = _make_service(repo=repo)

    result = service.search_nodes(template_id="tmpl-1", page=1)

    assert result.pagination.total == 1
    repo.count_nodes_by_template.assert_called_once_with(["tmpl-1"])
    repo.count_nodes.assert_not_called()


def test_search_nodes_q_filters_by_label_and_properties() -> None:
    """The ``q`` filter matches against label and stringified property values."""
    repo = MagicMock()
    repo.list_nodes.return_value = [
        _make_node("n1", label="Alpha Reactor"),
        _make_node("n2", label="Beta", description="contains alpha keyword"),
        _make_node("n3", label="Gamma", note="unrelated"),
    ]
    repo.count_nodes.return_value = 3
    service = _make_service(repo=repo)

    result = service.search_nodes(q="alpha")

    returned_ids = {n.id for n in result.data}
    # n1 matches on label, n2 matches on a property value, n3 is excluded.
    assert returned_ids == {"n1", "n2"}


def test_search_nodes_page_size_clamped_to_max() -> None:
    """page_size larger than max_page_size is clamped, and skip uses the clamp."""
    repo = MagicMock()
    repo.list_nodes.return_value = []
    repo.count_nodes.return_value = 0
    service = _make_service(repo=repo, settings=_make_settings(max_page_size=10))

    result = service.search_nodes(page=3, page_size=500)

    assert result.pagination.page_size == 10
    # skip = (page-1) * clamped_size = 2 * 10
    repo.list_nodes.assert_called_once_with(template_id=None, skip=20, limit=10)
    # total==0 -> total_pages defaults to 1
    assert result.pagination.total_pages == 1


# ---------------------------------------------------------------------------
# get_node_with_edges
# ---------------------------------------------------------------------------


def test_get_node_with_edges_returns_incoming_and_outgoing() -> None:
    """A found node returns its outgoing/incoming edges with counts."""
    node = _make_node("center")
    repo = MagicMock()
    repo.get_node.return_value = node
    outgoing = [_make_edge("e1", "center", "n2"), _make_edge("e2", "center", "n3")]
    incoming = [_make_edge("e3", "n4", "center")]
    repo.list_edges.side_effect = [outgoing, incoming]
    service = _make_service(repo=repo)

    result = service.get_node_with_edges("center")

    assert result.node.id == "center"
    assert result.total_outgoing == 2
    assert result.total_incoming == 1
    assert [e.id for e in result.outgoing_edges] == ["e1", "e2"]
    assert [e.id for e in result.incoming_edges] == ["e3"]


# ---------------------------------------------------------------------------
# search_edges
# ---------------------------------------------------------------------------


def test_search_edges_passes_filters_to_sql() -> None:
    """search_edges forwards both node filters to list_edges and count_edges."""
    repo = MagicMock()
    edges = [_make_edge("e1", "a", "b")]
    repo.list_edges.return_value = edges
    repo.count_edges.return_value = 1
    service = _make_service(repo=repo, settings=_make_settings(default_page_size=25))

    result = service.search_edges(source_node_id="a", target_node_id="b", page=1)

    assert [e.id for e in result.data] == ["e1"]
    assert result.pagination.total == 1
    repo.list_edges.assert_called_once_with(
        source_node_id="a", target_node_id="b", skip=0, limit=25
    )
    repo.count_edges.assert_called_once_with(source_node_id="a", target_node_id="b")


def test_search_edges_pagination_metadata_for_second_page() -> None:
    """Second page reports has_prev True and correct skip."""
    repo = MagicMock()
    repo.list_edges.return_value = []
    repo.count_edges.return_value = 30
    service = _make_service(repo=repo, settings=_make_settings(default_page_size=10))

    result = service.search_edges(page=2)

    assert result.pagination.page == 2
    assert result.pagination.has_prev is True
    assert result.pagination.has_next is True
    assert result.pagination.total_pages == 3
    repo.list_edges.assert_called_once_with(
        source_node_id=None, target_node_id=None, skip=10, limit=10
    )


# ---------------------------------------------------------------------------
# get_node_neighbors
# ---------------------------------------------------------------------------


def test_get_node_neighbors_both_directions_dedupes_and_hydrates() -> None:
    """Both-direction traversal collects unique neighbors and hydrates nodes."""
    center = _make_node("center")
    repo = MagicMock()
    repo.get_node.return_value = center

    out_edges = [_make_edge("e1", "center", "n2", label="LINKS")]
    in_edges = [
        _make_edge("e2", "n3", "center", label="MENTIONS"),
        # duplicate neighbor id n2 (already seen via outgoing) -> deduped
        _make_edge("e3", "n2", "center", label="DUP"),
    ]
    repo.list_edges.side_effect = [out_edges, in_edges]
    repo.get_nodes_batch.return_value = [_make_node("n2"), _make_node("n3")]
    service = _make_service(repo=repo)

    result = service.get_node_neighbors("center", direction="both")

    assert result.node_id == "center"
    assert result.direction == "both"
    # n2 (outgoing) + n3 (incoming); the duplicate n2 incoming edge is dropped.
    assert result.total == 2
    neighbor_ids = {n.node.id for n in result.neighbors}
    assert neighbor_ids == {"n2", "n3"}
    # The first-seen direction wins for n2 (outgoing).
    n2 = next(n for n in result.neighbors if n.node.id == "n2")
    assert n2.direction == "outgoing"
    assert n2.relationship_type == "LINKS"


def test_get_node_neighbors_outgoing_only_respects_limit() -> None:
    """direction='outgoing' only queries outgoing edges and honours the limit."""
    center = _make_node("center")
    repo = MagicMock()
    repo.get_node.return_value = center
    out_edges = [
        _make_edge("e1", "center", "n1"),
        _make_edge("e2", "center", "n2"),
        _make_edge("e3", "center", "n3"),
    ]
    repo.list_edges.return_value = out_edges
    repo.get_nodes_batch.return_value = [_make_node("n1"), _make_node("n2")]
    service = _make_service(repo=repo)

    result = service.get_node_neighbors("center", direction="outgoing", limit=2)

    # Only outgoing list_edges is queried (single call).
    repo.list_edges.assert_called_once_with(source_node_id="center", skip=0, limit=200)
    # limit=2 truncates the unique-edge collection to 2 ids.
    assert result.total == 2
    # Batch fetch only asked for the first 2 neighbor ids.
    repo.get_nodes_batch.assert_called_once_with(["n1", "n2"])


def test_get_node_neighbors_skips_missing_hydrated_nodes() -> None:
    """Neighbor ids absent from the batch fetch are silently dropped."""
    center = _make_node("center")
    repo = MagicMock()
    repo.get_node.return_value = center
    repo.list_edges.return_value = [
        _make_edge("e1", "center", "ghost"),
        _make_edge("e2", "center", "real"),
    ]
    # ``ghost`` is not returned by the batch fetch.
    repo.get_nodes_batch.return_value = [_make_node("real")]
    service = _make_service(repo=repo)

    result = service.get_node_neighbors("center", direction="outgoing")

    assert result.total == 1
    assert result.neighbors[0].node.id == "real"
