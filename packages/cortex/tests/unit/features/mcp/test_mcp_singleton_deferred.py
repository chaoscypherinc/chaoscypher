# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 4 Task H: MCP feature no longer creates objects at module-load time."""

from __future__ import annotations

import ast
from pathlib import Path


MCP_DIR = Path(__file__).resolve().parents[4] / "src" / "chaoscypher_cortex" / "features" / "mcp"


def _module_level_assignments(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def test_mcp_manager_is_not_a_module_level_singleton() -> None:
    assigns = _module_level_assignments(MCP_DIR / "service.py")
    assert "mcp_manager" not in assigns, (
        "mcp_manager should be a @functools.cache'd factory, not a module-level instance"
    )


def test_mcp_transport_is_not_a_module_level_singleton() -> None:
    assigns = _module_level_assignments(MCP_DIR / "api.py")
    assert "mcp_transport" not in assigns, (
        "mcp_transport should be a @functools.cache'd factory, not a module-level instance"
    )


def test_getters_exist() -> None:
    from chaoscypher_cortex.features.mcp import get_mcp_manager, get_mcp_transport

    assert callable(get_mcp_transport)
    assert callable(get_mcp_manager)


def test_getters_return_cached_singletons() -> None:
    from chaoscypher_cortex.features.mcp import get_mcp_manager, get_mcp_transport

    get_mcp_manager.cache_clear()
    get_mcp_transport.cache_clear()
    try:
        manager_a = get_mcp_manager()
        manager_b = get_mcp_manager()
        assert manager_a is manager_b

        app_a = get_mcp_transport()
        app_b = get_mcp_transport()
        assert app_a is app_b
    finally:
        get_mcp_manager.cache_clear()
        get_mcp_transport.cache_clear()
