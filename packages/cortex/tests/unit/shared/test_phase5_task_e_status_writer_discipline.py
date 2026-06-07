# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 5 Task E: source-status writes go through adapter state-machine methods.

Contract tests asserting that no Cortex runtime file mutates ``SourceRow.status``
directly via ``update_file``/``update_source`` with a ``SourceStatus`` value.
The canonical transitions live on the adapter:
``start_indexing`` / ``complete_indexing`` / ``fail_indexing`` (and the
analogous triples for extraction and commit), plus the two new Phase 5
methods ``cancel_extraction`` and ``abort_processing``.

Pure-AST contract tests — do not import Cortex runtime (sibling-worktree
editable-install collision would break collection).
"""

from __future__ import annotations

import ast
from pathlib import Path


CORTEX_SRC = Path(__file__).resolve().parents[3] / "src" / "chaoscypher_cortex"
CORE_ADAPTER_INDEXING = (
    Path(__file__).resolve().parents[5]
    / "packages"
    / "core"
    / "src"
    / "chaoscypher_core"
    / "adapters"
    / "sqlite"
    / "mixins"
    / "source_files_indexing.py"
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse(path: Path) -> ast.Module:
    return ast.parse(_source(path))


def _iter_cortex_py_files() -> list[Path]:
    return [p for p in CORTEX_SRC.rglob("*.py") if p.is_file()]


def _call_has_status_sourcestatus_kwarg(call: ast.Call) -> bool:
    """Return True if ``call`` has kwarg ``status=SourceStatus.<X>``."""
    for kw in call.keywords:
        if kw.arg != "status":
            continue
        if isinstance(kw.value, ast.Attribute) and isinstance(kw.value.value, ast.Name):
            if kw.value.value.id == "SourceStatus":
                return True
    return False


def _dict_has_status_sourcestatus(dct: ast.Dict) -> bool:
    """Return True if dict literal has a ``"status": SourceStatus.<X>`` entry."""
    for key, value in zip(dct.keys, dct.values, strict=False):
        if not isinstance(key, ast.Constant) or key.value != "status":
            continue
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            if value.value.id == "SourceStatus":
                return True
    return False


def test_no_update_file_with_sourcestatus_in_cortex() -> None:
    """No Cortex file calls update_file/update_source with status=SourceStatus.X.

    Bypasses the adapter state-machine methods. Use start_/complete_/fail_/
    cancel_extraction/abort_processing instead.
    """
    offenders: list[str] = []
    target_calls = {"update_file", "update_source"}

    for path in _iter_cortex_py_files():
        try:
            tree = _parse(path)
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # adapter.update_file(...) / adapter.update_source(...)
            if isinstance(func, ast.Attribute) and func.attr in target_calls:
                if _call_has_status_sourcestatus_kwarg(node):
                    offenders.append(
                        f"{path.relative_to(CORTEX_SRC)}:{node.lineno}: "
                        f".{func.attr}(..., status=SourceStatus.X) — "
                        f"use adapter state-machine methods instead"
                    )
                # Positional dict literal: update_file(source_id, {"status": SourceStatus.X})
                for arg in node.args:
                    if isinstance(arg, ast.Dict) and _dict_has_status_sourcestatus(arg):
                        offenders.append(
                            f"{path.relative_to(CORTEX_SRC)}:{node.lineno}: "
                            f".{func.attr}(..., {{'status': SourceStatus.X, ...}}) — "
                            f"use adapter state-machine methods instead"
                        )

    assert not offenders, (
        "Cortex must route SourceStatus transitions through adapter state-machine "
        "methods, not raw update_file/update_source dict writes:\n" + "\n".join(offenders)
    )


def test_no_source_status_attribute_write_in_cortex() -> None:
    """No Cortex file contains ``<something>.status = SourceStatus.<X>``.

    That pattern assigns to a SQLModel entity attribute, bypassing the
    state-machine methods and the session's change tracking. Adapter-side
    writes are a different module tree.
    """
    offenders: list[str] = []
    for path in _iter_cortex_py_files():
        try:
            tree = _parse(path)
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Attribute):
                    continue
                if target.attr != "status":
                    continue
                if not (
                    isinstance(node.value, ast.Attribute)
                    and isinstance(node.value.value, ast.Name)
                    and node.value.value.id == "SourceStatus"
                ):
                    continue
                offenders.append(
                    f"{path.relative_to(CORTEX_SRC)}:{node.lineno}: "
                    f".status = SourceStatus.X — use adapter state-machine methods"
                )

    assert not offenders, (
        "Cortex must not assign SourceStatus values to an entity .status attribute:\n"
        + "\n".join(offenders)
    )


def _mixin_class_method_names(path: Path, class_name: str) -> set[str]:
    tree = _parse(path)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
    raise AssertionError(f"class {class_name} not found in {path}")


def test_adapter_exposes_cancel_extraction_and_abort_processing() -> None:
    """The two new Phase 5 state-machine methods exist on SourceIndexingMixin."""
    names = _mixin_class_method_names(CORE_ADAPTER_INDEXING, "SourceIndexingMixin")
    required = {"cancel_extraction", "abort_processing"}
    missing = required - names
    assert not missing, (
        f"SourceIndexingMixin must expose {sorted(required)} (missing: {sorted(missing)})"
    )
