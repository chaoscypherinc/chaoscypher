# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 4 Task D: ``get_canvas_data`` business logic lives in GraphService."""

from __future__ import annotations

import ast
from pathlib import Path


GRAPH_API = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "chaoscypher_cortex"
    / "features"
    / "graph"
    / "api.py"
)
GRAPH_SERVICE = GRAPH_API.parent / "service.py"


def _handler_body_statement_count(tree: ast.AST, handler_name: str) -> int | None:
    """Return number of statements in the handler body, excluding the docstring."""
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == handler_name:
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body = body[1:]
            return len(body)
    return None


def test_graph_service_defines_get_canvas_data() -> None:
    tree = ast.parse(GRAPH_SERVICE.read_text(encoding="utf-8"))
    method_names = {
        n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "get_canvas_data" in method_names


def test_api_handler_stays_thin() -> None:
    """``get_canvas_data`` body stays a thin shim over the service call.

    Originally a 1-statement body (Phase 4 Task D extraction). 2026-05-19
    P0 added an ``asyncio.to_thread`` wrap so the blocking serialisation
    can't stall the FastAPI event loop, bumping the count to 2 (the
    to_thread call + the response wrap).
    """
    tree = ast.parse(GRAPH_API.read_text(encoding="utf-8"))
    count = _handler_body_statement_count(tree, "get_canvas_data")
    assert count == 2, (
        f"expected /canvas handler to have a 2-statement body "
        f"(asyncio.to_thread + response wrap), got {count}"
    )


def test_api_handler_no_longer_uses_graph_repository_directly() -> None:
    """The route handler delegates to the service — no ``.graph_repository`` touches."""
    tree = ast.parse(GRAPH_API.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_canvas_data":
            dump = ast.unparse(node)
            assert "graph_repository" not in dump, (
                "get_canvas_data handler still references graph_repository — "
                "business logic should live in GraphService.get_canvas_data"
            )
            return
    msg = "get_canvas_data handler not found"
    raise AssertionError(msg)
