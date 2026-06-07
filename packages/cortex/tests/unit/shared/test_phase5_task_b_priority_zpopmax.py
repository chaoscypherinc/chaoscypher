# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 5 Task B: priority convention flipped to ZPOPMAX.

Contract tests asserting that the scheduler pops the highest-scored task
first, that ``PrioritySettings`` encodes that ordering, and that no
``ZPOPMIN`` / ``zpopmin`` reference survives in the queue subsystem.

These tests are AST-based so they do not need to import the Cortex
runtime package — the sibling-worktree editable install in this
environment intercepts ``chaoscypher_core`` imports and breaks runtime
collection, as documented in Phase 3/4 retrospectives.
"""

from __future__ import annotations

import ast
from pathlib import Path


# Phase 5 Task B landed by relocating config + queue subsystems from Cortex
# into Core. The ZPOPMAX contract is unchanged; only the import paths shifted.
REPO_ROOT = Path(__file__).resolve().parents[4]
CORE_SRC = REPO_ROOT / "core" / "src" / "chaoscypher_core"
CONFIG_MODULE = CORE_SRC / "app_config" / "__init__.py"
QUEUE_DIR = CORE_SRC / "queue"
CLIENT_MODULE = QUEUE_DIR / "client.py"
WORKER_MODULE = QUEUE_DIR / "worker.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse(path: Path) -> ast.Module:
    return ast.parse(_source(path))


def _find_class(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _field_default_int(cls: ast.ClassDef, field_name: str) -> int:
    """Extract the ``default=...`` integer literal from a Field(...) assignment."""
    for node in cls.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name) or node.target.id != field_name:
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            raise AssertionError(f"{field_name} must be declared via Field(...)")
        for kw in call.keywords:
            if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, int):
                    return kw.value.value
        raise AssertionError(f"{field_name} has no integer default=")
    raise AssertionError(f"field {field_name} not found on class")


def test_priority_settings_ordering() -> None:
    """Interactive > background > default — matches ZPOPMAX."""
    cls = _find_class(_parse(CONFIG_MODULE), "PrioritySettings")
    interactive = _field_default_int(cls, "interactive")
    background = _field_default_int(cls, "background")
    default = _field_default_int(cls, "default")

    assert interactive > background > default, (
        f"PrioritySettings defaults must satisfy interactive > background > default "
        f"(got interactive={interactive} background={background} default={default}). "
        f"Higher numeric priority pops first under ZPOPMAX."
    )
    assert interactive == 100
    assert default == 1


def test_priority_settings_docstring_names_zpopmax() -> None:
    """The PrioritySettings docstring must document the ZPOPMAX convention."""
    cls = _find_class(_parse(CONFIG_MODULE), "PrioritySettings")
    doc = (ast.get_docstring(cls) or "").lower()
    assert "zpopmax" in doc, (
        "PrioritySettings docstring must document the ZPOPMAX convention so "
        "call sites can reason about pop order."
    )


def test_worker_poll_uses_zpopmax() -> None:
    """The poller calls ``self.client.zpopmax(...)`` (not zpopmin)."""
    src = _source(WORKER_MODULE)
    assert "self.client.zpopmax(" in src, (
        "worker._poll_queue must call self.client.zpopmax(...) — the pop "
        "direction is a Phase 5 contract."
    )
    assert "self.client.zpopmin(" not in src, (
        "worker module must not contain any self.client.zpopmin(...) call"
    )


def test_queue_subsystem_free_of_zpopmin_references() -> None:
    """Search the entire queue package for any ZPOPMIN/zpopmin reference."""
    offenders: list[str] = []
    for path in QUEUE_DIR.rglob("*.py"):
        text = _source(path)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "zpopmin" in line.lower():
                offenders.append(f"{path.relative_to(CORE_SRC)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "The queue subsystem must not reference ZPOPMIN (Phase 5 flipped to ZPOPMAX):\n"
        + "\n".join(offenders)
    )


def test_enqueue_score_uses_subtraction() -> None:
    """Score formula must subtract time fraction (ZPOPMAX — earlier = higher score)."""
    src = _source(CLIENT_MODULE)
    # Both single-enqueue and batch-enqueue paths encode priority minus time.
    assert src.count("float(priority) - time.time() / 1e10") >= 2, (
        "client.py must compute score as 'float(priority) - time.time() / 1e10' "
        "in both enqueue paths (single and batch) to preserve FIFO within a "
        "priority tier under ZPOPMAX."
    )
    assert "float(priority) + time.time() / 1e10" not in src, (
        "The old ZPOPMIN-era score formula (priority + time/1e10) must be gone."
    )
