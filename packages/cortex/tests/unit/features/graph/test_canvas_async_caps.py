# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""P0 2026-05-19 — canvas endpoint contract.

Three contracts:
1. ``GET /graph/canvas`` runs the synchronous ``GraphService.get_canvas_data``
   on a worker thread via ``asyncio.to_thread`` so the FastAPI event loop is
   not blocked during the (potentially long) SQLAlchemy fetch + Pydantic
   serialisation.
2. Pre-launch ``canvas_max_nodes`` / ``canvas_max_edges`` defaults stay at
   the lowered values (5_000 / 15_000) — the previous 100k / 300k caps
   materialised hundreds of MB of JSON on a single endpoint.
3. When an operator raises the canvas caps above the safe thresholds via
   ``settings.yaml``, the endpoint refuses to materialise a graph-wide
   payload unless the request scopes itself with an explicit ``source_ids``
   filter — otherwise a single anonymous request can still tank the box.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.settings import PaginationSettings
from chaoscypher_cortex.features.graph.service import (
    CANVAS_SAFE_EDGE_CAP,
    CANVAS_SAFE_NODE_CAP,
    GraphService,
)


def _make_settings_stub(*, max_nodes: int, max_edges: int) -> MagicMock:
    """Build a minimal ``Settings``-shaped stub for canvas-cap tests."""
    settings = MagicMock()
    settings.pagination.canvas_max_nodes = max_nodes
    settings.pagination.canvas_max_edges = max_edges
    return settings


def _make_repo_stub() -> MagicMock:
    """Build a ``GraphRepository`` stub that never hits the database.

    The threshold-gate fires before any repo method is touched in the
    blocking-cap test, so these stubs only matter for the "gate lets
    a scoped query through" case.
    """
    repo = MagicMock()
    repo.count_nodes.return_value = 0
    repo.count_edges.return_value = 0
    repo.list_nodes.return_value = []
    repo.list_edges.return_value = []
    repo.list_templates.return_value = []
    return repo


GRAPH_API = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "chaoscypher_cortex"
    / "features"
    / "graph"
    / "api.py"
)


def test_canvas_handler_uses_asyncio_to_thread() -> None:
    """``get_canvas_data`` dispatches the service call via ``asyncio.to_thread``.

    Otherwise a request with ``canvas_max_nodes`` worth of rows would
    block the FastAPI event loop for the duration of the SQLAlchemy
    fetch + Pydantic serialisation — every other /api/ request stalls.
    """
    source = GRAPH_API.read_text(encoding="utf-8")
    assert "asyncio.to_thread" in source, (
        "GET /graph/canvas must dispatch GraphService.get_canvas_data via "
        "asyncio.to_thread to avoid blocking the FastAPI event loop"
    )

    tree = ast.parse(source)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_canvas_data":
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "to_thread"
                ):
                    found = True
                    break
    assert found, "get_canvas_data must call asyncio.to_thread on the service method"


def test_pagination_canvas_defaults_lowered_for_pre_launch() -> None:
    """Defaults reduced to 5_000 / 15_000 to bound endpoint blast radius."""
    s = PaginationSettings()
    assert s.canvas_max_nodes == 5_000
    assert s.canvas_max_edges == 15_000


def test_canvas_caps_can_still_be_raised_via_settings() -> None:
    """Operators can opt in to the legacy caps explicitly via settings.yaml."""
    s = PaginationSettings(canvas_max_nodes=100_000, canvas_max_edges=300_000)
    assert s.canvas_max_nodes == 100_000
    assert s.canvas_max_edges == 300_000


def test_threshold_gate_rejects_unscoped_request_above_safe_node_cap() -> None:
    """Raising canvas_max_nodes without a source_ids filter must fail fast.

    Defends against the regression where an operator opts back in to the
    legacy 100k cap (or anywhere above ``CANVAS_SAFE_NODE_CAP``) and an
    anonymous client tanks the box with a single request.
    """
    settings = _make_settings_stub(
        max_nodes=CANVAS_SAFE_NODE_CAP + 1,
        max_edges=CANVAS_SAFE_EDGE_CAP,
    )
    repo = _make_repo_stub()
    service = GraphService(repo, adapter=None, settings=settings)

    with pytest.raises(ValidationError) as exc_info:
        service.get_canvas_data(source_ids=None)

    assert exc_info.value.field == "source_ids"
    assert "source_ids" in str(exc_info.value)
    # Repo must NOT be touched — the gate fires before the heavy load.
    repo.list_nodes.assert_not_called()
    repo.list_edges.assert_not_called()


def test_threshold_gate_rejects_unscoped_request_above_safe_edge_cap() -> None:
    """Edge-cap path mirrors the node-cap path."""
    settings = _make_settings_stub(
        max_nodes=CANVAS_SAFE_NODE_CAP,
        max_edges=CANVAS_SAFE_EDGE_CAP + 1,
    )
    repo = _make_repo_stub()
    service = GraphService(repo, adapter=None, settings=settings)

    with pytest.raises(ValidationError):
        service.get_canvas_data(source_ids=[])


def test_threshold_gate_passes_when_source_ids_present() -> None:
    """An explicit ``source_ids`` filter unlocks the high-cap path."""
    settings = _make_settings_stub(
        max_nodes=100_000,
        max_edges=300_000,
    )
    repo = _make_repo_stub()
    service = GraphService(repo, adapter=None, settings=settings)

    # Should not raise — the filter scopes the blast radius.
    result = service.get_canvas_data(source_ids=["src-1", "src-2"])

    assert result["truncated"] is False
    assert result["nodes"] == []
    assert result["edges"] == []
    repo.list_nodes.assert_called_once()
    repo.list_edges.assert_called_once()


def test_threshold_gate_passes_at_safe_defaults_without_source_ids() -> None:
    """Default 5k/15k caps without source_ids must remain allowed."""
    settings = _make_settings_stub(
        max_nodes=CANVAS_SAFE_NODE_CAP,
        max_edges=CANVAS_SAFE_EDGE_CAP,
    )
    repo = _make_repo_stub()
    service = GraphService(repo, adapter=None, settings=settings)

    result = service.get_canvas_data(source_ids=None)

    assert result["truncated"] is False
    repo.list_nodes.assert_called_once()
    repo.list_edges.assert_called_once()


@pytest.mark.asyncio
async def test_route_offloads_service_call_to_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route handler MUST dispatch the heavy service call via ``asyncio.to_thread``.

    Otherwise the synchronous SQLAlchemy fetch + Pydantic serialisation
    blocks the FastAPI event loop and every other /api/ request stalls
    for the duration. Mocking ``asyncio.to_thread`` directly is the
    cleanest assertion that the wrapping isn't accidentally dropped.
    """
    from chaoscypher_cortex.features.graph import api as graph_api

    captured: dict = {}

    async def fake_to_thread(func, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["func"] = func
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {
            "truncated": False,
            "nodes": [],
            "edges": [],
            "templates": [],
            "total_nodes": 0,
            "total_edges": 0,
        }

    monkeypatch.setattr(graph_api.asyncio, "to_thread", fake_to_thread)

    service = MagicMock(spec=GraphService)
    service.get_canvas_data = MagicMock()

    response = await graph_api.get_canvas_data(
        _="test-user",
        graph_service=service,
        source_ids=None,
    )

    # to_thread was invoked with the sync service method.
    assert captured["func"] is service.get_canvas_data
    assert captured["kwargs"] == {"source_ids": None}
    # Crucially: the sync method itself was NOT called directly from the
    # event loop — only ``to_thread`` saw it.
    service.get_canvas_data.assert_not_called()
    # Response is the model wrap of the dispatched payload.
    assert response.truncated is False
