# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for `chaoscypher node delete`."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock, call

from click.testing import CliRunner


delete_module = import_module("chaoscypher_cli.commands.node.delete")


def _edge_page(edge_ids: list[str], *, page: int, total_pages: int) -> dict[str, object]:
    return {
        "data": [{"id": edge_id} for edge_id in edge_ids],
        "pagination": {
            "page": page,
            "page_size": len(edge_ids),
            "total_pages": total_pages,
            "has_next": page < total_pages,
        },
    }


def test_delete_cascade_pages_all_connected_edges(monkeypatch) -> None:
    """Cascade delete removes every incoming/outgoing page before deleting the node."""
    edge_service = MagicMock()
    edge_service.list_edges.side_effect = [
        _edge_page(["out-1"], page=1, total_pages=2),
        _edge_page(["out-2"], page=2, total_pages=2),
        _edge_page(["in-1"], page=1, total_pages=2),
        _edge_page(["in-2"], page=2, total_pages=2),
    ]
    node_service = MagicMock()
    node_service.get_node.return_value = {
        "id": "node-1",
        "label": "Node 1",
        "template_id": "tpl",
    }
    ctx = SimpleNamespace(node_service=node_service, edge_service=edge_service)

    monkeypatch.setattr(delete_module, "get_context", lambda database_name: ctx)
    monkeypatch.setattr(
        delete_module,
        "get_settings",
        lambda: SimpleNamespace(cli=SimpleNamespace(edge_batch_size=1)),
    )

    result = CliRunner().invoke(delete_module.delete, ["node-1", "--cascade", "--force"])

    assert result.exit_code == 0, result.output
    assert edge_service.list_edges.call_args_list == [
        call(source_node_id="node-1", page=1, page_size=1),
        call(source_node_id="node-1", page=2, page_size=1),
        call(target_node_id="node-1", page=1, page_size=1),
        call(target_node_id="node-1", page=2, page_size=1),
    ]
    assert edge_service.delete_edge.call_args_list == [
        call("out-1"),
        call("out-2"),
        call("in-1"),
        call("in-2"),
    ]
    node_service.delete_node.assert_called_once_with("node-1")


def test_delete_cascade_deduplicates_self_loop_edges(monkeypatch) -> None:
    """A self-loop appears in both directions but should be deleted only once."""
    edge_service = MagicMock()
    edge_service.list_edges.side_effect = [
        _edge_page(["loop-1"], page=1, total_pages=1),
        _edge_page(["loop-1"], page=1, total_pages=1),
    ]
    node_service = MagicMock()
    node_service.get_node.return_value = {
        "id": "node-1",
        "label": "Node 1",
        "template_id": "tpl",
    }
    ctx = SimpleNamespace(node_service=node_service, edge_service=edge_service)

    monkeypatch.setattr(delete_module, "get_context", lambda database_name: ctx)
    monkeypatch.setattr(
        delete_module,
        "get_settings",
        lambda: SimpleNamespace(cli=SimpleNamespace(edge_batch_size=100)),
    )

    result = CliRunner().invoke(delete_module.delete, ["node-1", "--cascade", "--force"])

    assert result.exit_code == 0, result.output
    edge_service.delete_edge.assert_called_once_with("loop-1")
