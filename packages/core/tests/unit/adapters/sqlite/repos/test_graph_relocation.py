# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 3 Task C: GraphRepository relocation contract test.

Locks in the move of ``GraphRepository`` and ``remove_corrupt_nodes`` from
``chaoscypher_core.repos.graph`` to
``chaoscypher_core.adapters.sqlite.repos``. Lives under the adapter layer
because the implementation binds directly to SQLite-specific SQLModel
entities (``GraphNode``, ``GraphEdge``, ``GraphTemplate``) and composes
mixins that issue raw SQL against that schema.

Asserts:
1. ``chaoscypher_core.adapters.sqlite.repos.GraphRepository`` is
   importable and is a class.
2. ``chaoscypher_core.adapters.sqlite.repos.remove_corrupt_nodes`` is
   importable and callable.
3. ``GraphRepository`` exposes the public surface callers rely on
   (representative methods like ``create_node``, ``create_edge``,
   ``create_template``) — checked at the class level via ``hasattr``
   with no instantiation.
4. The old source directory ``packages/core/src/chaoscypher_core/repos/graph/``
   is gone on disk.
5. The old import path ``chaoscypher_core.repos.graph`` is no longer
   importable (ModuleNotFoundError).
6. An AST scan of all source and test files under ``packages/*``
   confirms no remaining ImportFrom statements reference the old
   module path.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest


# Resolve the repo root from this file's path. The file lives at
# packages/core/tests/unit/adapters/sqlite/repos/test_graph_relocation.py
# (7 parent steps from the file → repo root).
PROJECT_ROOT = Path(__file__).resolve().parents[7]
PACKAGES_DIR = PROJECT_ROOT / "packages"


# ---------------------------------------------------------------------------
# 1. New barrel exposes GraphRepository
# ---------------------------------------------------------------------------


def test_new_barrel_exposes_graph_repository() -> None:
    module = importlib.import_module("chaoscypher_core.adapters.sqlite.repos")

    assert hasattr(module, "GraphRepository"), (
        "chaoscypher_core.adapters.sqlite.repos must export GraphRepository"
    )
    assert inspect.isclass(module.GraphRepository)


# ---------------------------------------------------------------------------
# 2. New barrel exposes remove_corrupt_nodes
# ---------------------------------------------------------------------------


def test_new_barrel_exposes_remove_corrupt_nodes() -> None:
    module = importlib.import_module("chaoscypher_core.adapters.sqlite.repos")

    assert hasattr(module, "remove_corrupt_nodes"), (
        "chaoscypher_core.adapters.sqlite.repos must export remove_corrupt_nodes"
    )
    assert callable(module.remove_corrupt_nodes)


# ---------------------------------------------------------------------------
# 3. GraphRepository exposes the public surface callers depend on
# ---------------------------------------------------------------------------


def test_graph_repository_public_surface() -> None:
    """Representative methods composed by the mixins must still be reachable.

    Structural check only — no instantiation (which would require a
    SafeSession bound to a SQLite engine).
    """
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository

    expected_methods = (
        # Node operations
        "create_node",
        "get_node",
        "list_nodes",
        "update_node",
        "delete_node",
        "upsert_nodes_batch",
        # Edge operations
        "create_edge",
        "get_edge",
        "list_edges",
        "update_edge",
        "delete_edge",
        "upsert_edges_batch",
        # Template operations
        "create_template",
        "get_template",
        "list_templates",
        "update_template",
        "delete_template",
        "upsert_template",
        # Repository-level utilities
        "clear_all",
        "count_nodes",
        "count_edges",
        "count_templates",
    )
    missing = [name for name in expected_methods if not hasattr(GraphRepository, name)]
    assert not missing, f"GraphRepository is missing expected methods: {missing}"


# ---------------------------------------------------------------------------
# 4. Old directory is gone
# ---------------------------------------------------------------------------


def test_old_graph_repo_directory_deleted() -> None:
    old_dir = PACKAGES_DIR / "core" / "src" / "chaoscypher_core" / "repos" / "graph"
    assert not old_dir.exists(), (
        f"old graph repo directory must be deleted but still exists: {old_dir}"
    )


# ---------------------------------------------------------------------------
# 5. Old module path is not importable
# ---------------------------------------------------------------------------


def test_old_graph_module_not_importable() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("chaoscypher_core.repos.graph")


# ---------------------------------------------------------------------------
# 6. AST scan — no source/test file still imports from the old path
# ---------------------------------------------------------------------------


def _iter_python_files() -> list[Path]:
    """Yield every .py file under packages/*/src and packages/*/tests."""
    files: list[Path] = []
    for pkg in PACKAGES_DIR.iterdir():
        if not pkg.is_dir():
            continue
        for sub in ("src", "tests"):
            root = pkg / sub
            if not root.exists():
                continue
            files.extend(root.rglob("*.py"))
    return files


def _imports_from_old_graph(path: Path) -> list[str]:
    """Return any ImportFrom.module strings that start with the old path."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):  # fmt: skip
        return []

    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "chaoscypher_core.repos.graph" or node.module.startswith(
                "chaoscypher_core.repos.graph."
            ):
                hits.append(node.module)
    return hits


def test_no_source_or_test_file_imports_from_old_graph_path() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _iter_python_files():
        hits = _imports_from_old_graph(path)
        if hits:
            offenders[str(path.relative_to(PROJECT_ROOT))] = hits

    assert not offenders, (
        "Found ImportFrom statements referencing the old "
        "chaoscypher_core.repos.graph.* path:\n"
        + "\n".join(f"  {file}: {modules}" for file, modules in sorted(offenders.items()))
    )
