# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for `chaoscypher node get`."""

from __future__ import annotations

import json
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock, call

from click.testing import CliRunner


get_module = import_module("chaoscypher_cli.commands.node.get")


def _edge_page(edge_ids: list[str], *, page: int, total_pages: int) -> dict[str, object]:
    return {
        "data": [
            {"id": edge_id, "source_node_id": "node-1", "target_node_id": edge_id}
            for edge_id in edge_ids
        ],
        "pagination": {
            "page": page,
            "page_size": len(edge_ids),
            "total_pages": total_pages,
            "has_next": page < total_pages,
        },
    }


def test_get_include_links_pages_all_connected_edges(monkeypatch) -> None:
    """`--include-links` must walk every page, not just the first, per direction."""
    edge_service = MagicMock()
    edge_service.list_edges.side_effect = [
        _edge_page(["out-1"], page=1, total_pages=2),
        _edge_page(["out-2"], page=2, total_pages=2),
        _edge_page(["in-1"], page=1, total_pages=1),
    ]
    node_service = MagicMock()
    node_service.get_node.return_value = {
        "id": "node-1",
        "label": "Node 1",
        "template_id": "tpl",
    }
    ctx = SimpleNamespace(node_service=node_service, edge_service=edge_service)

    monkeypatch.setattr(get_module, "get_context", lambda database_name: ctx)
    monkeypatch.setattr(
        get_module,
        "get_settings",
        lambda: SimpleNamespace(cli=SimpleNamespace(edge_batch_size=1)),
    )

    result = CliRunner().invoke(get_module.get, ["node-1", "--include-links", "--format", "json"])

    assert result.exit_code == 0, result.output
    # Every page in each direction is requested (no first-page truncation).
    assert edge_service.list_edges.call_args_list == [
        call(source_node_id="node-1", page=1, page_size=1),
        call(source_node_id="node-1", page=2, page_size=1),
        call(target_node_id="node-1", page=1, page_size=1),
    ]
    # All three edges (both outgoing pages + the incoming page) are in the output.
    payload = json.loads(result.output)
    link_ids = {link["id"] for link in payload["links"]}
    assert link_ids == {"out-1", "out-2", "in-1"}
