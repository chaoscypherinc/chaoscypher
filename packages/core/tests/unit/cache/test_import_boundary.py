# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The cache package imports only the leaf layers of core; the factories import it, never the reverse."""

from __future__ import annotations

import ast
from pathlib import Path

import chaoscypher_core.cache as cache_pkg


_ALLOWED = (
    "chaoscypher_core.cache",
    "chaoscypher_core.models",
    "chaoscypher_core.ports",
    "chaoscypher_core.utils",
    "chaoscypher_core.exceptions",
    "chaoscypher_core.settings",
)


def _core_imports(path: Path) -> list[str]:
    """Every ``chaoscypher_*`` module ``path`` imports, type-only imports included."""
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return [n for n in names if n.startswith("chaoscypher")]


def test_the_cache_package_imports_only_leaf_layers() -> None:
    root = Path(cache_pkg.__file__).parent
    files = sorted(root.rglob("*.py"))
    assert files
    offenders = {
        f"{path.name}: {name}"
        for path in files
        for name in _core_imports(path)
        if not any(name == a or name.startswith(f"{a}.") for a in _ALLOWED)
    }
    assert offenders == set()
