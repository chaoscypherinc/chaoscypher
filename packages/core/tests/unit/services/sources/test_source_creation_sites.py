# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Guard: every production call that creates a source row applies the caller's labels.

Parses every ``packages/*/src/**/*.py`` and collects the calls to
``.create_source(``, ``.upload_source(``, ``.upsert_source_by_ccx_iri(`` and
``.create_url_placeholder(`` with their enclosing function. The set must
equal :data:`SITES`: a new way
for a document to arrive fails here until it applies ``apply_source_labels``
(or its caller one level up does), or it is pinned as an exemption with its
reason.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[6]
CREATING = {"create_source", "upload_source", "upsert_source_by_ccx_iri", "create_url_placeholder"}
APPLY = re.compile(r"\b(apply_source_labels|label_new_source)\(")

APPLIES = "applies labels"
"""The enclosing function itself calls ``apply_source_labels`` (or ``label_new_source``, built on it)."""

# (file, enclosing function) -> APPLIES, ("caller", "<function in the same file>"),
# or ("exempt", "<reason>").
SITES: dict[tuple[str, str], object] = {
    (
        "packages/cli/src/chaoscypher_cli/sources/service.py",
        "CLISourceProcessingService.upload_file",
    ): APPLIES,
    (
        "packages/cli/src/chaoscypher_cli/sources/service.py",
        "CLISourceProcessingService.upload_url",
    ): APPLIES,
    (
        "packages/core/src/chaoscypher_core/services/sources/management/service.py",
        "SourceProcessingService.upload_file",
    ): APPLIES,
    ("packages/core/src/chaoscypher_core/mcp/server.py", "_run_index_only_pipeline"): APPLIES,
    ("packages/core/src/chaoscypher_core/bootstrap.py", "Engine._ensure_source_row"): (
        "caller",
        "Engine.process_document",
    ),
    (
        "packages/core/src/chaoscypher_core/services/package/importer/service.py",
        "CcxImporter._upsert_source",
    ): (
        "exempt",
        "the package importer restores the package's own tags and summary (Task 9)",
    ),
    (
        "packages/core/src/chaoscypher_core/services/graph/management/source.py",
        "SourceService.create_source",
    ): ("exempt", "no production caller"),
    (
        "packages/core/src/chaoscypher_core/operations/sources/url_fetch_handler.py",
        "import_url_page",
    ): (
        "exempt",
        "transient placeholder, replaced by the upload",
    ),
}


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function in the module by qualified name (``Class.method`` or ``function``)."""
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                found[f"{prefix}{child.name}"] = child
                visit(child, f"{prefix}{child.name}.")

    visit(tree, "")
    return found


class _CallFinder(ast.NodeVisitor):
    """Records each creating call with the innermost function (``Class.method``) holding it."""

    def __init__(self) -> None:
        self.scope: list[str] = []
        self.owners: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815 — ast.NodeVisitor's name

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr in CREATING:
            self.owners.add(".".join(self.scope) or "<module>")
        self.generic_visit(node)


def _creation_sites() -> dict[tuple[str, str], str]:
    """Each (file, enclosing function) that calls a creating method, with that function's source."""
    sites: dict[tuple[str, str], str] = {}
    for path in sorted(REPO.glob("packages/*/src/**/*.py")):
        source = path.read_text(encoding="utf-8")
        if not any(f".{name}(" in source for name in CREATING):
            continue
        tree = ast.parse(source)
        finder = _CallFinder()
        finder.visit(tree)
        functions = _functions(tree)
        rel = path.relative_to(REPO).as_posix()
        for owner in finder.owners:
            func = functions.get(owner)
            sites[(rel, owner)] = ast.get_source_segment(source, func) or "" if func else ""
    return sites


def _function_source(rel: str, qualname: str) -> str:
    source = (REPO / rel).read_text(encoding="utf-8")
    return ast.get_source_segment(source, _functions(ast.parse(source))[qualname]) or ""


def test_every_source_creation_call_applies_labels() -> None:
    sites = _creation_sites()

    assert set(sites) == set(SITES), (
        "A source-creating call was added or moved. Make its function (or its caller) "
        "call apply_source_labels, or pin it in SITES with the reason it is exempt."
    )
    for (rel, qualname), rule in SITES.items():
        if rule == APPLIES:
            assert APPLY.search(sites[(rel, qualname)]), f"{rel}::{qualname} does not apply labels"
        else:
            assert isinstance(rule, tuple)
            kind, detail = rule
            if kind == "caller":
                assert APPLY.search(_function_source(rel, detail)), (
                    f"{rel}::{detail} does not apply"
                )
                assert f".{qualname.rsplit('.', 1)[-1]}(" in _function_source(rel, detail)
            else:
                assert kind == "exempt" and detail
