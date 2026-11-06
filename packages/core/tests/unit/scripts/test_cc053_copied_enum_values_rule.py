# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Self-tests for CC053 — a literal string list that copies a StrEnum's or Literal's values.

The rule lives in ``check_copied_enum_values`` inside ``scripts/lint_claude_rules.py``.
0.7.0 shipped MCP ``create_template`` with a ``property_type`` enum copied by hand from
``PropertyType``; the copy fell behind and ``datetime`` was refused.

Pins:
- a schema ``"enum"``, a ``click.Choice`` and an inline ``Literal`` copying a source fire;
- a near copy fires too (the 0.7.0 list had one value the source never had);
- the alias definition itself, unrelated lists and two common words of a big enum are silent;
- ``# noqa: CC053 - reason`` opts out; a bare marker does not;
- tests and files outside a ``src`` tree are out of scope;
- the sources are read from the real packages, and the tree is clean.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _load_linter() -> Any:
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "lint_claude_rules.py"
    spec = importlib.util.spec_from_file_location("lint_claude_rules", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LINTER = _load_linter()

_SOURCES = {
    frozenset({"node", "edge"}): "example.models.ElementKind",
    frozenset(
        {"string", "text", "integer", "float", "boolean", "date", "datetime", "enum", "json"}
    ): "example.models.PropertyType",
}


def _check(source: str, tmp_path: Path, where: str = "packages/core/src/example/tools.py") -> list:
    target = tmp_path / where
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    violations: list[Any] = _LINTER.check_copied_enum_values(target, ast.parse(source), _SOURCES)
    return violations


@pytest.mark.parametrize(
    "source",
    [
        'SCHEMA = {"type": "string", "enum": ["node", "edge"]}\n',
        'import click\nopt = click.option("--kind", type=click.Choice(["edge", "node"]))\n',
        'from typing import Literal\ndef f(kind: Literal["node", "edge"]) -> None: ...\n',
        # the 0.7.0 list: five PropertyType values and "number", which it never had
        'S = {"enum": ["string", "text", "number", "boolean", "date", "enum"]}\n',
    ],
    ids=["schema-enum", "click-choice", "inline-literal", "near-copy"],
)
def test_a_copy_fires(source: str, tmp_path: Path) -> None:
    (violation,) = _check(source, tmp_path)
    assert violation.rule == "CC053"
    assert "example.models." in violation.message


@pytest.mark.parametrize(
    "source",
    [
        'from typing import Literal\nElementKind = Literal["node", "edge"]\n',
        'S = {"enum": schema_enum(ElementKind)}\n',
        'S = {"enum": ["table", "json", "yaml"]}\n',
        'S = {"enum": ["text", "json"]}\n',  # two common words of a nine-value enum
        'S = {"enum": ["node", "chunk"]}\n',  # one shared value
        'S = {"enum": ["node", "edge"]}  # noqa: CC053 - the canvas picker, not ElementKind\n',
        'S = {\n    "enum": [  # noqa: CC053 - local to this tool\n        "node",\n        "edge",\n    ]\n}\n',
    ],
    ids=["alias-definition", "derived", "unrelated", "common-words", "one-shared", "noqa", "noqa-multiline"],
)  # fmt: skip
def test_a_non_copy_is_silent(source: str, tmp_path: Path) -> None:
    assert _check(source, tmp_path) == []


def test_a_bare_marker_needs_a_reason(tmp_path: Path) -> None:
    (violation,) = _check('S = {"enum": ["node", "edge"]}  # noqa: CC053\n', tmp_path)
    assert "reason" in violation.message


@pytest.mark.parametrize(
    "where", ["packages/core/tests/unit/test_x.py", "scripts/helper.py"], ids=["tests", "no-src"]
)
def test_out_of_scope_files_are_silent(where: str, tmp_path: Path) -> None:
    assert _check('S = {"enum": ["node", "edge"]}\n', tmp_path, where) == []


def test_the_sources_come_from_the_packages() -> None:
    sources = _LINTER._load_value_sources()
    names = set(sources.values())
    assert "chaoscypher_core.models.PropertyType" in names  # a StrEnum
    assert "chaoscypher_core.models.ElementKind" in names  # a Literal alias


def test_the_tree_is_clean() -> None:
    root = Path(__file__).resolve().parents[5] / "packages"
    violations = [
        v
        for path in _LINTER.find_python_files([root])
        for v in _LINTER.check_copied_enum_values(path, ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert violations == [], "\n".join(map(str, violations))
