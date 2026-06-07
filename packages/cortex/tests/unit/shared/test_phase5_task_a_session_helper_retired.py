# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 5 Task A: get_db_session / get_session wrappers retired.

Contract tests asserting that the legacy
``chaoscypher_cortex.shared.database.session.get_db_session`` and
``get_session`` wrappers no longer exist, and that no Cortex runtime
file imports them.
"""

from __future__ import annotations

import ast
from pathlib import Path


CORTEX_SRC = Path(__file__).resolve().parents[3] / "src" / "chaoscypher_cortex"
SESSION_MODULE = CORTEX_SRC / "shared" / "database" / "session.py"
DATABASE_INIT = CORTEX_SRC / "shared" / "database" / "__init__.py"
LEGACY_NAMES = frozenset({"get_db_session", "get_session"})


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_session_module_no_longer_defines_legacy_helpers() -> None:
    tree = _parse(SESSION_MODULE)
    func_names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "get_db_session" not in func_names, "get_db_session must be deleted from session.py"
    assert "get_session" not in func_names, "get_session must be deleted from session.py"
    assert "get_current_session" in func_names, "get_current_session must still exist"


def test_database_barrel_does_not_re_export_legacy_helpers() -> None:
    tree = _parse(DATABASE_INIT)
    exported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, ast.List | ast.Tuple):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                exported.add(elt.value)
    for legacy in LEGACY_NAMES:
        assert legacy not in exported, (
            f"{legacy!r} must not appear in chaoscypher_cortex.shared.database.__all__"
        )


def _iter_python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def test_no_cortex_file_imports_legacy_session_helpers() -> None:
    offenders: list[str] = []
    for path in _iter_python_files(CORTEX_SRC):
        # The session module is the legitimate home for get_current_session;
        # but it must no longer define the legacy helpers (covered by the
        # separate test above). Skip it here so we test imports only.
        if path == SESSION_MODULE:
            continue
        try:
            tree = _parse(path)
        except SyntaxError:  # pragma: no cover - unreachable in a healthy tree
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if "database.session" in module or module.endswith("shared.database"):
                    for alias in node.names:
                        if alias.name in LEGACY_NAMES:
                            offenders.append(
                                f"{path.relative_to(CORTEX_SRC)}: from {module} import {alias.name}"
                            )
    assert not offenders, (
        "Cortex runtime files must not import the legacy session helpers:\n" + "\n".join(offenders)
    )
