# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Self-tests for CC002 — entity-style attribute access on a value returned
from a storage protocol call (which returns a ``TypedDict``).

The rule lives in ``ClaudeRulesChecker.visit_Attribute`` inside
``scripts/lint_claude_rules.py``. Storage protocols return
dict-shaped rows; accessing ``.field`` on them is an
``AttributeError`` waiting to happen.

These tests pin:
- ``self.storage.get_*()`` → ``var.attr`` fires CC002.
- Dict-safe attributes (``.get``, ``.items``, ...) are exempt.
- Test files are out of scope (fixtures commonly use raw dicts).
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


def _load_linter():
    """Import scripts/lint_claude_rules.py as a module."""
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "lint_claude_rules.py"
    spec = importlib.util.spec_from_file_location("lint_claude_rules", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LINTER = _load_linter()


_BAD_SOURCE = """
class SomeService:
    def __init__(self, storage):
        self.storage = storage

    def show(self, source_id):
        source = self.storage.get_source(source_id)
        return source.title  # CC002: entity-style access on a dict
"""

_OK_DICT_SOURCE = """
class SomeService:
    def __init__(self, storage):
        self.storage = storage

    def show(self, source_id):
        source = self.storage.get_source(source_id)
        return source["title"]  # canonical dict access — no CC002
"""

_OK_DICT_METHOD_SOURCE = """
class SomeService:
    def __init__(self, storage):
        self.storage = storage

    def show(self, source_id):
        source = self.storage.get_source(source_id)
        # .get / .items / etc. are dict methods — allowed
        return source.get("title")
"""

_OK_NON_STORAGE_CALL = """
class SomeService:
    def __init__(self, repo):
        self.repo = repo

    def show(self, source_id):
        # repo (not storage) returns an ORM entity — out of scope.
        source = self.repo.get(source_id)
        return source.title
"""


def _check(source: str, file_path: Path) -> list:
    """Run the AST checker against a source string at the given path."""
    tree = ast.parse(source)
    checker = _LINTER.ClaudeRulesChecker(file_path)
    checker.visit(tree)
    return [v for v in checker.violations if v.rule == "CC002"]


def test_cc002_flags_entity_attr_on_storage_dict(tmp_path: Path) -> None:
    """``source.title`` after ``self.storage.get_source(...)`` triggers CC002."""
    target = tmp_path / "packages" / "cortex" / "src" / "feature" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_BAD_SOURCE)
    violations = _check(_BAD_SOURCE, target)
    assert len(violations) == 1, f"Expected 1 CC002 violation; got {violations}"
    assert "source.title" in violations[0].message
    assert "dict" in violations[0].message.lower()


def test_cc002_silent_on_dict_subscript(tmp_path: Path) -> None:
    """Dict-style ``source["title"]`` access is the canonical pattern — no CC002."""
    target = tmp_path / "packages" / "cortex" / "src" / "feature" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_OK_DICT_SOURCE)
    violations = _check(_OK_DICT_SOURCE, target)
    assert violations == [], f"Dict subscript should not fire CC002; got {violations}"


def test_cc002_silent_on_dict_methods(tmp_path: Path) -> None:
    """``source.get(...)`` / ``.items()`` / ``.keys()`` are dict methods — allowed."""
    target = tmp_path / "packages" / "cortex" / "src" / "feature" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_OK_DICT_METHOD_SOURCE)
    violations = _check(_OK_DICT_METHOD_SOURCE, target)
    assert violations == [], f"Dict method access should not fire CC002; got {violations}"


def test_cc002_silent_on_non_storage_call(tmp_path: Path) -> None:
    """Calls other than ``self.storage.<verb>_*`` are out of scope."""
    target = tmp_path / "packages" / "cortex" / "src" / "feature" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_OK_NON_STORAGE_CALL)
    violations = _check(_OK_NON_STORAGE_CALL, target)
    assert violations == [], (
        f"Attribute access on a non-storage call should not fire CC002; got {violations}"
    )


def test_cc002_skips_test_files(tmp_path: Path) -> None:
    """The rule explicitly skips paths containing 'tests' (fixtures use raw dicts)."""
    target = tmp_path / "packages" / "cortex" / "tests" / "unit" / "test_thing.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_BAD_SOURCE)
    violations = _check(_BAD_SOURCE, target)
    assert violations == [], f"Test files must be out of CC002 scope; got {violations}"
