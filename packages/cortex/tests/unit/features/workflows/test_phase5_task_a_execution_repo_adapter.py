# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 5 Task A: WorkflowExecutionRepository takes SqliteAdapter.

Contract tests confirming the Phase 5 Task A migration:

- WorkflowExecutionRepository no longer depends on ``get_db_session`` or
  on a raw ``database_name``; it receives a connected ``SqliteAdapter``
  at construction time.
- The ``repository.py`` module does not import the Cortex session
  wrapper.
- Every call site either threads an adapter through or acquires one
  via ``get_sqlite_adapter`` and disconnects in a ``finally`` block.
"""

from __future__ import annotations

import ast
from pathlib import Path


# Phase 5 Task A landed by relocating the workflow execution repository,
# orchestrator, and operations service from Cortex into Core. The contract
# is unchanged; only the import paths shifted.
REPO_ROOT = Path(__file__).resolve().parents[5]
CORE_SRC = REPO_ROOT / "core" / "src" / "chaoscypher_core"
REPO_PATH = CORE_SRC / "operations" / "workflows" / "repository.py"
ORCHESTRATOR_PATH = CORE_SRC / "operations" / "workflows" / "orchestrator.py"
OPERATIONS_PATH = CORE_SRC / "operations" / "workflow_operations_service.py"


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _find_class(tree: ast.Module, name: str) -> ast.ClassDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def test_repository_does_not_import_get_db_session() -> None:
    tree = _parse(REPO_PATH)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "database.session" in node.module:
                raise AssertionError(
                    "WorkflowExecutionRepository must not import the Cortex session wrapper"
                )
            for alias in node.names:
                assert alias.name != "get_db_session", (
                    "WorkflowExecutionRepository must not import get_db_session"
                )


def test_workflow_execution_repository_constructor_accepts_adapter() -> None:
    tree = _parse(REPO_PATH)
    cls = _find_class(tree, "WorkflowExecutionRepository")
    assert cls is not None, "WorkflowExecutionRepository class must exist"

    init = next(
        (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"),
        None,
    )
    assert init is not None, "WorkflowExecutionRepository must define __init__"

    arg_names = [a.arg for a in init.args.args]
    assert "adapter" in arg_names, (
        "WorkflowExecutionRepository.__init__ must accept an 'adapter' argument, "
        f"got args={arg_names!r}"
    )
    assert "database_name" not in arg_names, (
        "WorkflowExecutionRepository no longer takes database_name"
    )


def test_orchestrator_constructs_repo_with_adapter_and_disconnects() -> None:
    source = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert "get_sqlite_adapter(database_name=database_name)" in source, (
        "orchestrator must acquire adapter via get_sqlite_adapter"
    )
    assert "WorkflowExecutionRepository(exec_adapter)" in source, (
        "orchestrator must construct repo with adapter"
    )
    assert "exec_adapter.disconnect()" in source, "orchestrator must disconnect adapter in finally"


def test_workflow_operations_service_constructs_repo_with_adapter() -> None:
    source = OPERATIONS_PATH.read_text(encoding="utf-8")
    assert "WorkflowExecutionRepository(idempotency_adapter)" in source, (
        "workflow_operations_service must construct repo with adapter"
    )
    assert "idempotency_adapter.disconnect()" in source, (
        "workflow_operations_service must disconnect the idempotency adapter"
    )
