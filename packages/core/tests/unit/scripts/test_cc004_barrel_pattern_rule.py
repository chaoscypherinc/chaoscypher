# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Self-tests for CC004 — ``__init__.py`` files with exports must define
``__all__`` for barrel-pattern compliance.

The rule lives in ``check_barrel_pattern`` inside
``scripts/lint_claude_rules.py``.
"""

from __future__ import annotations

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


_INIT_WITH_EXPORTS_NO_ALL = '''\
"""Some package barrel."""

from .foo import Foo
from .bar import Bar
from .baz import Baz
'''

_INIT_WITH_EXPORTS_AND_ALL = '''\
"""Some package barrel."""

from .foo import Foo
from .bar import Bar

__all__ = ["Foo", "Bar"]
'''

_INIT_EMPTY = ""

_INIT_MINIMAL = '''\
"""Minimal package init."""
'''

_NON_INIT_FILE = """\
from foo import bar
"""


def test_cc004_flags_init_with_exports_missing_all(tmp_path: Path) -> None:
    """``__init__.py`` with multi-line ``from ... import ...`` and no ``__all__`` triggers CC004."""
    init = tmp_path / "__init__.py"
    init.write_text(_INIT_WITH_EXPORTS_NO_ALL)
    violations = _LINTER.check_barrel_pattern(init)
    cc004 = [v for v in violations if v.rule == "CC004"]
    assert len(cc004) == 1, f"Expected 1 CC004 violation; got {violations}"
    assert "__all__" in cc004[0].message


def test_cc004_silent_when_all_present(tmp_path: Path) -> None:
    """``__all__`` is defined → no violation."""
    init = tmp_path / "__init__.py"
    init.write_text(_INIT_WITH_EXPORTS_AND_ALL)
    violations = _LINTER.check_barrel_pattern(init)
    assert violations == [], f"__all__ present should suppress CC004; got {violations}"


def test_cc004_silent_on_empty_init(tmp_path: Path) -> None:
    """Empty ``__init__.py`` is allowed (no exports, no rule)."""
    init = tmp_path / "__init__.py"
    init.write_text(_INIT_EMPTY)
    violations = _LINTER.check_barrel_pattern(init)
    assert violations == [], f"Empty __init__.py should not trigger CC004; got {violations}"


def test_cc004_silent_on_minimal_init(tmp_path: Path) -> None:
    """Single-line docstring-only ``__init__.py`` is below the threshold — no rule."""
    init = tmp_path / "__init__.py"
    init.write_text(_INIT_MINIMAL)
    violations = _LINTER.check_barrel_pattern(init)
    assert violations == [], f"Minimal __init__.py should not trigger CC004; got {violations}"


def test_cc004_silent_on_non_init_file(tmp_path: Path) -> None:
    """The rule is scoped to ``__init__.py`` only."""
    other = tmp_path / "regular_module.py"
    other.write_text(_NON_INIT_FILE)
    violations = _LINTER.check_barrel_pattern(other)
    assert violations == [], f"Non-__init__ files are out of scope; got {violations}"
