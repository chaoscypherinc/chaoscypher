# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 4 Task E: ``SearchService.search()`` owns the keyword/semantic/hybrid branch."""

from __future__ import annotations

import ast
from pathlib import Path


SEARCH_API = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "chaoscypher_cortex"
    / "features"
    / "search"
    / "api.py"
)
SEARCH_SERVICE = SEARCH_API.parent / "service.py"


def test_search_service_defines_dispatch_method() -> None:
    tree = ast.parse(SEARCH_SERVICE.read_text(encoding="utf-8"))
    method_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method_names.add(node.name)
    assert "search" in method_names, (
        "SearchService must define a unified `search(query, limit, search_type)` dispatch method"
    )


def test_handler_has_no_branching_or_embedding_wiring() -> None:
    """The route handler must not build the embedding callback or branch on search_type.

    Both responsibilities moved into ``SearchService.search()``. The handler
    delegates and translates errors.
    """
    tree = ast.parse(SEARCH_API.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "search":
            dump = ast.unparse(node)
            assert "get_embedding_service" not in dump, (
                "search handler still constructs an embedding callback — "
                "move this into SearchService.search()"
            )
            assert "semantic_search" not in dump, (
                "search handler still branches to semantic_search — "
                "move the branch into SearchService.search()"
            )
            assert "hybrid_search" not in dump, (
                "search handler still branches to hybrid_search — "
                "move the branch into SearchService.search()"
            )
            return
    msg = "search handler not found"
    raise AssertionError(msg)
