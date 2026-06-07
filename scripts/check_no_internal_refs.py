#!/usr/bin/env python3
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Fail if any shipped source file references a private internal/ doc path.

The private docs tree is stripped from the public export, so an in-code
reference to one of its subdirectories becomes a dangling pointer in the
public repo and leaks private planning / investigation filenames. This guard
runs over the shipped trees (packages/, e2e/) as a pre-commit hook and in the
local CI sweep. Replace any flagged reference with a self-contained summary or
a public-docs link.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


# Private subdirectories under the docs tree that must never be referenced by
# shipped code. The pattern is assembled at runtime so this file does not
# contain the forbidden literal itself (it lives in scripts/, which is not
# scanned, but assembling keeps the guard self-consistent).
_PRIVATE_SUBDIRS = (
    "plans",
    "notes",
    "company",
    "adrs",
    "specs",
    "archive",
    "investigation",
    "standards",
    "procedures",
    "mockups",
    "claude",
    "package-todos",
    "test_fixtures",
)
_DOCS_DIR = "internal"
_PATTERN = re.compile(rf"{_DOCS_DIR}/(?:{'|'.join(_PRIVATE_SUBDIRS)})/")

_ROOTS = ("packages", "e2e", "scripts", "tools", ".github")
_ROOT_FILES = (
    "README.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CLA.md",
    "CHANGELOG.md",
    "Makefile",
    "pyproject.toml",
    ".pre-commit-config.yaml",
    ".gitleaks.toml",
    ".dockerignore",
)
_EXTS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".md",
    ".mdx",
    ".yml",
    ".yaml",
    ".json",
    ".sh",
    ".toml",
}
_SKIP_DIRS = {"node_modules", "__pycache__", "dist", ".venv", "coverage", "test-output"}


def find_internal_refs(
    repo_root: Path,
    roots: tuple[str, ...] = _ROOTS,
    root_files: tuple[str, ...] = _ROOT_FILES,
) -> list[str]:
    """Return ``path:lineno: line`` strings for every private-docs reference found."""
    paths_to_scan: list[Path] = []
    for root in roots:
        base = repo_root / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_dir() or path.suffix not in _EXTS:
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            paths_to_scan.append(path)
    for name in root_files:
        path = repo_root / name
        if path.is_file():
            paths_to_scan.append(path)

    hits: list[str] = []
    for path in paths_to_scan:
        try:
            text = path.read_text(encoding="utf-8")
        # Parenthesized so the guard parses on Python <=3.13 (the public export
        # may run on older interpreters). Under the repo's py314 ruff target,
        # PEP 758 lets ruff strip these parens, reintroducing a <=3.13
        # SyntaxError -- so pin the line against the formatter.
        except (UnicodeDecodeError, OSError):  # fmt: skip
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if _PATTERN.search(line):
                hits.append(f"{path.relative_to(repo_root)}:{lineno}: {line.strip()}")
    return hits


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    hits = find_internal_refs(repo_root)
    if hits:
        print("Shipped files reference a private docs path (stripped from the public export):")
        for hit in hits:
            print(f"  {hit}")
        print("\nReplace with a self-contained summary or a public-docs link.")
        return 1

    print("OK: no private docs references in shipped trees.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
