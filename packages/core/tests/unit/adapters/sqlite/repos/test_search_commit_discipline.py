# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 3 Task E: SearchRepository must not call ``conn.commit()`` directly.

The Phase 3 adapter-boundary plan requires that any write under
``chaoscypher_core/`` either participate in the caller's session or run
through a transactional context manager that commits on clean exit.
``SearchRepository`` previously called ``conn.commit()`` explicitly in
five places. It now uses ``self._engine.begin()`` everywhere the caller
isn't providing a session; ``begin()`` commits on clean exit and rolls
back on exception, so no raw ``.commit()`` remains.
"""

from __future__ import annotations

import ast
from pathlib import Path


SEARCH_REPO_FILE = (
    Path(__file__).resolve().parents[5]
    / "src"
    / "chaoscypher_core"
    / "adapters"
    / "sqlite"
    / "repos"
    / "search.py"
)


def test_search_repository_file_exists() -> None:
    assert SEARCH_REPO_FILE.exists(), f"expected {SEARCH_REPO_FILE} to exist"


def test_search_repository_has_no_conn_commit_calls() -> None:
    """AST scan: no ``<anything>.commit()`` call survives in search.py."""
    tree = ast.parse(SEARCH_REPO_FILE.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "commit"
            and not node.args
            and not node.keywords
        ):
            offenders.append(f"line {node.lineno}: <...>.commit() call")
    assert not offenders, (
        "Direct .commit() calls still present in SearchRepository:\n  " + "\n  ".join(offenders)
    )


def test_search_repository_uses_engine_begin() -> None:
    """The standalone path uses ``self._engine.begin()``, not ``.connect()`` without commit.

    Sanity check — asserts the file references ``self._engine.begin()`` at least
    once so we know the migration did happen and wasn't silently no-op'd.
    """
    source = SEARCH_REPO_FILE.read_text(encoding="utf-8")
    assert "self._engine.begin()" in source
