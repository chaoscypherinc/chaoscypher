# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Guard: a tag is found by name in one place, ``services/sources/tags.py``.

Three callers once listed every tag and matched names by hand (the engine's
``create_tag``, the package importer and the CLI chat's ``--tag``), each with
its own idea of case. They now go through ``find_tags`` / ``get_or_create_tags``.
This test fails when a new ``.list_tags(`` call or a hand-written name match
appears anywhere in the shipped source.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[6]
_THE_LOOKUP = "packages/core/src/chaoscypher_core/services/sources/tags.py"

# (file, enclosing function) of every allowed ``.list_tags(`` call, and why.
_ALLOWED_LIST_TAGS: dict[tuple[str, str], str] = {
    (
        "packages/core/src/chaoscypher_core/services/graph/management/source.py",
        "list_tags",
    ): "SourceService.list_tags: the engine method behind the tags list route",
    (
        "packages/cortex/src/chaoscypher_cortex/features/sources/tag_service.py",
        "list_tags",
    ): "the Cortex tags list route's service passes the whole list through",
    (
        "packages/cortex/src/chaoscypher_cortex/features/sources/tags_api.py",
        "list_tags",
    ): "the Cortex tags list route (GET /tags) returns every tag",
    (
        "packages/cli/src/chaoscypher_cli/commands/chat.py",
        "_available_tags_hint",
    ): "the CLI chat's 'available tags' message after an unknown --tag, no lookup",
}

# A tag's name matched by hand: ``t["name"].lower()`` or ``{t["name"]: t["id"] ...}``.
_NAME_MATCH = re.compile(
    r"""\[["']name["']\]\.lower\(\)|\[["']name["']\][ \t]*:[ \t]*\w+\[["']id["']\]"""
)


def _shipped_sources() -> list[Path]:
    return sorted(_ROOT.glob("packages/*/src/**/*.py"))


def _relative(path: Path) -> str:
    return path.relative_to(_ROOT).as_posix()


def _list_tags_calls(tree: ast.AST) -> list[tuple[str, int]]:
    """(enclosing function, line) of every ``<x>.list_tags(...)`` call."""
    calls: list[tuple[str, int]] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = function
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                inner = child.name
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "list_tags"
            ):
                calls.append((function, child.lineno))
            visit(child, inner)

    visit(tree, "<module>")
    return calls


def test_the_scan_sees_the_shapes() -> None:
    tree = ast.parse("def f(s):\n    return s.storage.list_tags('db')\n")
    assert _list_tags_calls(tree) == [("f", 2)]
    assert _NAME_MATCH.search('by_name = {tag["name"].lower(): tag["id"] for tag in tags}')
    assert _NAME_MATCH.search("existing = {t['name']: t['id'] for t in tags}")
    assert not _NAME_MATCH.search('template["name"].strip()')
    assert not _NAME_MATCH.search('if a["name"] != b["name"]:\n    a["id"] = 1')


def test_no_other_tag_name_lookup() -> None:
    sources = _shipped_sources()
    assert any(_relative(p) == _THE_LOOKUP for p in sources), "the scan found no sources"

    unexpected_calls = []
    seen_allowed: set[tuple[str, str]] = set()
    name_matches = []
    for path in sources:
        relative = _relative(path)
        text = path.read_text(encoding="utf-8")
        if relative != _THE_LOOKUP:
            name_matches += [
                f"{relative}:{text.count(chr(10), 0, m.start()) + 1}"
                for m in _NAME_MATCH.finditer(text)
            ]
        if ".list_tags(" not in text:
            continue
        for function, line in _list_tags_calls(ast.parse(text)):
            if relative == _THE_LOOKUP or (relative, function) in _ALLOWED_LIST_TAGS:
                seen_allowed.add((relative, function))
            else:
                unexpected_calls.append(f"{relative}:{line} in {function}()")

    assert not unexpected_calls, (
        "find a tag by name through services/sources/tags.py (find_tags / "
        f"get_or_create_tags), not by listing every tag: {unexpected_calls}"
    )
    assert not name_matches, f"a tag name matched by hand outside the lookup: {name_matches}"
    stale = set(_ALLOWED_LIST_TAGS) - seen_allowed
    assert not stale, f"allowed list_tags call sites that no longer exist: {sorted(stale)}"
