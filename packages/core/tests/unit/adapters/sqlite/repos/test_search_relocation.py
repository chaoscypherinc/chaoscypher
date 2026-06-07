# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 3 Task B: SearchRepository relocation contract test.

Locks in the move of ``SearchRepository`` and ``extract_searchable_text`` from
``chaoscypher_core.repos.search`` to
``chaoscypher_core.adapters.sqlite.repos``. This is an adapter-layer
concern because the repository is SQLite-specific (FTS5 + sqlite-vec).

Asserts:
1. The new adapter barrel exposes ``SearchRepository`` and
   ``extract_searchable_text``.
2. The implementation class still satisfies
   :class:`chaoscypher_core.ports.search.SearchRepositoryProtocol`
   (checked at the class level via ``hasattr`` — no instantiation).
3. The old source directory is gone on disk.
4. The old import path ``chaoscypher_core.repos.search`` is no longer
   importable (ModuleNotFoundError).
5. An AST scan of all source and test files under ``packages/*`` confirms
   no remaining ImportFrom statements reference the old module path.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest


# Resolve the repo root from this file's path. The file lives at
# packages/core/tests/unit/adapters/sqlite/repos/test_search_relocation.py
# (7 parent steps from the file → repo root).
PROJECT_ROOT = Path(__file__).resolve().parents[7]
PACKAGES_DIR = PROJECT_ROOT / "packages"


# ---------------------------------------------------------------------------
# 1. New barrel exposes SearchRepository and extract_searchable_text
# ---------------------------------------------------------------------------


def test_new_barrel_exposes_search_repository() -> None:
    module = importlib.import_module("chaoscypher_core.adapters.sqlite.repos")

    assert hasattr(module, "SearchRepository"), (
        "chaoscypher_core.adapters.sqlite.repos must export SearchRepository"
    )
    assert inspect.isclass(module.SearchRepository)


def test_new_barrel_exposes_extract_searchable_text() -> None:
    module = importlib.import_module("chaoscypher_core.adapters.sqlite.repos")

    assert hasattr(module, "extract_searchable_text"), (
        "chaoscypher_core.adapters.sqlite.repos must export extract_searchable_text"
    )
    assert callable(module.extract_searchable_text)


# ---------------------------------------------------------------------------
# 2. Implementation class matches the port protocol (class-level check)
# ---------------------------------------------------------------------------


def test_search_repository_satisfies_protocol_surface() -> None:
    """Every method defined on SearchRepositoryProtocol must exist on the class.

    This is a structural check — we do not instantiate the repository
    (which would require a SQLAlchemy engine + FTS5/vec extensions).
    """
    from chaoscypher_core.adapters.sqlite.repos import SearchRepository
    from chaoscypher_core.ports.search import SearchRepositoryProtocol

    # Collect the concrete method/property surface the protocol declares
    protocol_members = {
        name
        for name, value in vars(SearchRepositoryProtocol).items()
        if not name.startswith("_") and (callable(value) or isinstance(value, property))
    }

    missing = [name for name in protocol_members if not hasattr(SearchRepository, name)]
    assert not missing, f"SearchRepository is missing protocol members: {missing}"


# ---------------------------------------------------------------------------
# 3. Old directory is gone
# ---------------------------------------------------------------------------


def test_old_search_repo_directory_deleted() -> None:
    old_dir = PACKAGES_DIR / "core" / "src" / "chaoscypher_core" / "repos" / "search"
    assert not old_dir.exists(), (
        f"old search repo directory must be deleted but still exists: {old_dir}"
    )


# ---------------------------------------------------------------------------
# 4. Old module path is not importable
# ---------------------------------------------------------------------------


def test_old_search_module_not_importable() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("chaoscypher_core.repos.search")


# ---------------------------------------------------------------------------
# 5. AST scan — no source/test file still imports from the old path
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


def _imports_from_old_search(path: Path) -> list[str]:
    """Return any ImportFrom.module strings that start with the old path."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):  # fmt: skip
        return []

    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "chaoscypher_core.repos.search" or node.module.startswith(
                "chaoscypher_core.repos.search."
            ):
                hits.append(node.module)
    return hits


def test_no_source_or_test_file_imports_from_old_search_path() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _iter_python_files():
        hits = _imports_from_old_search(path)
        if hits:
            offenders[str(path.relative_to(PROJECT_ROOT))] = hits

    assert not offenders, (
        "Found ImportFrom statements referencing the old "
        "chaoscypher_core.repos.search.* path:\n"
        + "\n".join(f"  {file}: {modules}" for file, modules in sorted(offenders.items()))
    )
