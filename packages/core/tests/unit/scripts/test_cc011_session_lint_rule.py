# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the tightened CC011 — Session() construction outside
the standalone-repo carve-out.

Part of Workstream C / Decision 5 of the 2026-04-23 architecture audit.
Pins: (a) Session(...) in mixins/adapter code is caught, (b)
Session(self._engine) inside adapters/sqlite/repos/*.py is permitted,
(c) other Session(anything_else) inside that dir is still caught,
(d) `# noqa: CC011` on the line suppresses the violation.
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

_MIXIN_SOURCE = """
from sqlmodel import Session


class BadMixin:
    def write(self, engine):
        with Session(engine) as s:
            s.commit()
"""

_STANDALONE_REPO_SOURCE = """
from sqlmodel import Session


class GoodRepo:
    def __init__(self, engine):
        self._engine = engine

    def write(self):
        with Session(self._engine) as s:
            s.commit()
"""

_STANDALONE_REPO_BAD_ARG_SOURCE = """
from sqlmodel import Session


class SneakyRepo:
    def __init__(self, engine):
        self._engine = engine

    def write(self, other):
        with Session(other) as s:
            s.commit()
"""

_MIXIN_NOQA_SOURCE = """
from sqlmodel import Session


class LegacyMixin:
    def write(self, engine):
        with Session(engine) as s:  # noqa: CC011
            s.commit()
"""

_MIXIN_SELF_ENGINE_SOURCE = """
from sqlmodel import Session


class SneakyMixin:
    def __init__(self, engine):
        self._engine = engine

    def write(self):
        with Session(self._engine) as s:
            s.commit()
"""

_STANDALONE_REPO_KWARGS_SOURCE = """
from sqlmodel import Session


class KwargsRepo:
    def __init__(self, engine):
        self._engine = engine

    def write(self):
        with Session(bind=self._engine) as s:
            s.commit()
"""


def _write(tmp_path: Path, relpath: str, source: str) -> Path:
    target = tmp_path / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return target


def test_cc011_catches_session_construction_in_mixin(tmp_path: Path) -> None:
    """Session(...) inside adapters/sqlite/mixins/*.py triggers CC011."""
    target = _write(
        tmp_path,
        "packages/core/src/chaoscypher_core/adapters/sqlite/mixins/widgets.py",
        _MIXIN_SOURCE,
    )
    tree = ast.parse(_MIXIN_SOURCE)
    violations = _LINTER.check_session_construction_outside_standalone_repo(target, tree)
    assert len(violations) == 1, (
        f"Expected 1 CC011 violation for Session() in a mixin, got {len(violations)}"
    )
    assert violations[0].rule == "CC011"


def test_cc011_allows_session_self_engine_in_standalone_repo(tmp_path: Path) -> None:
    """Session(self._engine) inside adapters/sqlite/repos/*.py is the permitted pattern."""
    target = _write(
        tmp_path,
        "packages/core/src/chaoscypher_core/adapters/sqlite/repos/graph_snapshot.py",
        _STANDALONE_REPO_SOURCE,
    )
    tree = ast.parse(_STANDALONE_REPO_SOURCE)
    violations = _LINTER.check_session_construction_outside_standalone_repo(target, tree)
    assert violations == [], (
        f"Session(self._engine) in a standalone repo should be allowed; got {violations}"
    )


def test_cc011_still_catches_session_non_self_engine_in_standalone_repo(tmp_path: Path) -> None:
    """Only Session(self._engine) is whitelisted — other args still trigger CC011."""
    target = _write(
        tmp_path,
        "packages/core/src/chaoscypher_core/adapters/sqlite/repos/graph_snapshot.py",
        _STANDALONE_REPO_BAD_ARG_SOURCE,
    )
    tree = ast.parse(_STANDALONE_REPO_BAD_ARG_SOURCE)
    violations = _LINTER.check_session_construction_outside_standalone_repo(target, tree)
    assert len(violations) == 1, (
        f"Session(other) — even in a repos/ file — should still trigger CC011; got {violations}"
    )


def test_cc011_catches_session_self_engine_outside_repos_dir(tmp_path: Path) -> None:
    """Session(self._engine) is only whitelisted in adapters/sqlite/repos/ — not mixins/."""
    target = _write(
        tmp_path,
        "packages/core/src/chaoscypher_core/adapters/sqlite/mixins/widgets.py",
        _MIXIN_SELF_ENGINE_SOURCE,
    )
    tree = ast.parse(_MIXIN_SELF_ENGINE_SOURCE)
    violations = _LINTER.check_session_construction_outside_standalone_repo(target, tree)
    assert len(violations) == 1, (
        f"Session(self._engine) in a mixin (not repos/) should still trigger CC011; "
        f"got {violations}"
    )
    assert violations[0].rule == "CC011"


def test_cc011_catches_session_kwargs_form_in_standalone_repo(tmp_path: Path) -> None:
    """Carve-out is literal Session(self._engine) — Session(bind=self._engine) is not whitelisted."""
    target = _write(
        tmp_path,
        "packages/core/src/chaoscypher_core/adapters/sqlite/repos/graph_snapshot.py",
        _STANDALONE_REPO_KWARGS_SOURCE,
    )
    tree = ast.parse(_STANDALONE_REPO_KWARGS_SOURCE)
    violations = _LINTER.check_session_construction_outside_standalone_repo(target, tree)
    assert len(violations) == 1, (
        f"Session(bind=self._engine) — kwargs form — is not the carve-out; got {violations}"
    )


def test_cc011_respects_noqa_on_line(tmp_path: Path) -> None:
    """A line-level `# noqa: CC011` suppresses the rule — same mechanism used by mixin_base.py:58."""
    target = _write(
        tmp_path,
        "packages/core/src/chaoscypher_core/adapters/sqlite/mixins/widgets.py",
        _MIXIN_NOQA_SOURCE,
    )
    tree = ast.parse(_MIXIN_NOQA_SOURCE)
    violations = _LINTER.check_session_construction_outside_standalone_repo(target, tree)
    assert violations == [], (
        f"`# noqa: CC011` on the Session(...) line should suppress; got {violations}"
    )


def test_cc011_ignores_files_outside_adapters_sqlite(tmp_path: Path) -> None:
    """CC011 Session-construction check is scoped to adapters/sqlite/*."""
    target = _write(
        tmp_path,
        "packages/cortex/src/chaoscypher_cortex/features/graph/service.py",
        _MIXIN_SOURCE,
    )
    tree = ast.parse(_MIXIN_SOURCE)
    violations = _LINTER.check_session_construction_outside_standalone_repo(target, tree)
    assert violations == [], (
        f"Session(...) outside adapters/sqlite/ is not CC011's scope; got {violations}"
    )
