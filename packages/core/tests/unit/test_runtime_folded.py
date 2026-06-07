# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract test: chaoscypher_runtime is folded into chaoscypher_core.

After the 2026-04-20 fold, no source file in the workspace may import
chaoscypher_runtime. This is enforced by AST walk rather than a grep so
string literals (e.g., in docstrings referring to historical naming) don't
trigger false positives.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
PACKAGE_SOURCE_ROOTS = [
    WORKSPACE_ROOT / "packages" / "core" / "src",
    WORKSPACE_ROOT / "packages" / "cortex" / "src",
    WORKSPACE_ROOT / "packages" / "neuron" / "src",
    WORKSPACE_ROOT / "packages" / "cli" / "src",
]


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for root in PACKAGE_SOURCE_ROOTS:
        if not root.exists():
            continue
        files.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return files


def _file_imports_runtime(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.startswith("chaoscypher_runtime") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("chaoscypher_runtime"):
                return True
    return False


def test_no_source_file_imports_chaoscypher_runtime() -> None:
    """After the fold, no package under packages/{core,cortex,neuron,cli}/src
    may import chaoscypher_runtime. If this fails, the offender either
    copy-pasted from an old branch or the fold was partial.
    """
    violators = [str(p) for p in _iter_py_files() if _file_imports_runtime(p)]
    assert not violators, (
        "The following files still import chaoscypher_runtime. "
        "Rewrite to chaoscypher_core.<equivalent> per the 2026-04-20 fold:\n"
        + "\n".join(f"  - {v}" for v in violators)
    )


def test_runtime_package_is_uninstallable() -> None:
    """chaoscypher_runtime no longer exists as an importable module."""
    with pytest.raises(ModuleNotFoundError):
        __import__("chaoscypher_runtime")
