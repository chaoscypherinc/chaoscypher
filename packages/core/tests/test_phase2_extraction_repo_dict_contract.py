# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ExtractionRepository.store_entity_embeddings returns dicts.

Originally a Phase 2 Task J contract test; kept and relocated in Phase 3
after ``ExtractionRepository`` moved into
``chaoscypher_core.adapters.sqlite.repos``. The module-level "no SQLModel
import" assertion is intentionally gone — the repository lives inside the
adapter now, so the SQLModel import belongs there. What still matters is
the return-shape contract callers depend on.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np


REPO_FILE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "chaoscypher_core"
    / "adapters"
    / "sqlite"
    / "repos"
    / "extraction.py"
)


def test_store_entity_embeddings_returns_list_of_dicts() -> None:
    from chaoscypher_core.adapters.sqlite.repos.extraction import ExtractionRepository

    session = MagicMock()
    repo = ExtractionRepository(session=session, database_name="test_db")

    result = repo.store_entity_embeddings(
        source_id="src_123",
        entity_metadata=[{"entity_index": 0, "entity_id": "ent_a"}],
        embeddings=[np.array([0.1, 0.2, 0.3], dtype=np.float32)],
        embedding_model="text-embedding-3-small",
        embedding_dimensions=3,
    )

    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], dict)
    assert result[0]["source_id"] == "src_123"
    assert result[0]["entity_index"] == 0
    assert result[0]["entity_id"] == "ent_a"
    assert result[0]["embedding_model"] == "text-embedding-3-small"
    assert result[0]["embedding_dimensions"] == 3
    # Raw bytes are intentionally excluded from the projection
    assert "embedding" not in result[0]


def test_store_entity_embeddings_accepts_list_of_floats() -> None:
    """Cached embeddings loaded from chunk_extraction_tasks.raw_entity_embeddings
    arrive as plain Python lists (JSON deserialization). ExtractionRepository
    must coerce them rather than calling .astype() on a list and crashing with
    AttributeError. Regression for 2026-05-15.
    """
    from chaoscypher_core.adapters.sqlite.repos.extraction import ExtractionRepository

    session = MagicMock()
    repo = ExtractionRepository(session=session, database_name="test_db")

    # Pure Python list — what JSON deserialization yields.
    result = repo.store_entity_embeddings(
        source_id="src_456",
        entity_metadata=[{"entity_index": 0, "entity_id": "ent_b"}],
        embeddings=[[0.1, 0.2, 0.3]],
        embedding_model="text-embedding-3-small",
        embedding_dimensions=3,
    )

    assert len(result) == 1
    assert result[0]["entity_id"] == "ent_b"


def test_return_type_annotation_is_list_dict() -> None:
    source = REPO_FILE.read_text(encoding="utf-8")
    assert "list[dict[str, Any]]:" in source
    assert "list[SourceEntityEmbedding]" not in source


def test_repo_barrel_exports_extraction_repository() -> None:
    from chaoscypher_core.adapters.sqlite.repos import ExtractionRepository as FromBarrel
    from chaoscypher_core.adapters.sqlite.repos.extraction import (
        ExtractionRepository as FromModule,
    )

    assert FromBarrel is FromModule


def test_old_repos_extraction_module_is_gone() -> None:
    """Phase 3 deleted chaoscypher_core.repos.extraction entirely."""
    import importlib

    old_path = (
        Path(__file__).resolve().parents[1] / "src" / "chaoscypher_core" / "repos" / "extraction"
    )
    # Check for source files only, not stale __pycache__ left over from
    # previous Python runs in dev checkouts. Docker builds start clean
    # so this distinction doesn't matter there, but locally a stale
    # __pycache__/*.pyc directory would otherwise fail this guard.
    leftover_py_files = list(old_path.glob("**/*.py")) if old_path.exists() else []
    assert not leftover_py_files, (
        f"Old extraction repo package still has source files: {leftover_py_files}"
    )

    try:
        importlib.import_module("chaoscypher_core.repos.extraction")
    except ModuleNotFoundError:
        pass
    else:  # pragma: no cover
        msg = "chaoscypher_core.repos.extraction is still importable"
        raise AssertionError(msg)


def test_no_runtime_imports_of_old_extraction_path() -> None:
    """AST scan: no file under packages/*/src or packages/*/tests imports the old path."""
    repo_root = Path(__file__).resolve().parents[3]
    targets = [
        repo_root / "packages" / "core" / "src",
        repo_root / "packages" / "core" / "tests",
        repo_root / "packages" / "cortex" / "src",
        repo_root / "packages" / "cortex" / "tests",
        repo_root / "packages" / "cli" / "src",
        repo_root / "packages" / "neuron" / "src",
    ]
    bad: list[str] = []
    for root in targets:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # fmt: skip
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module is not None
                    and node.module.startswith("chaoscypher_core.repos.extraction")
                ):
                    bad.append(f"{path}:{node.lineno} (module={node.module})")
    assert not bad, "Old extraction repo path still referenced:\n" + "\n".join(bad)
