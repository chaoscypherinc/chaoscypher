#!/usr/bin/env python3
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Fail if any shipped source file references a private internal/ doc path or private repo.

The private docs tree is stripped from the public export, so an in-code
reference to one of its subdirectories becomes a dangling pointer in the
public repo and leaks private planning / investigation filenames. A private
repository's name leaks the same way. This guard runs over the shipped trees
(packages/, e2e/) as a pre-commit hook and in the local CI sweep, and the
public export gate reuses it. Replace any flagged reference with a
self-contained summary or a public-docs link.

The private repo names were checked only by the export gate until
2026-10-17, so a test docstring naming the dev repo passed every commit and
PR check and blocked that week's public snapshot (#808).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


# Private subdirectories under the docs tree that must never be referenced by
# shipped code. The pattern is assembled at runtime so this file does not
# contain the forbidden literal itself — load-bearing, not just
# self-consistency: scripts/ IS in _ROOTS below, so this file scans itself.
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
    "loops",
    "metrics",
    "migrations",
)
_DOCS_DIR = "internal"
_PATTERN = re.compile(rf"{_DOCS_DIR}/(?:{'|'.join(_PRIVATE_SUBDIRS)})/")

# Private repositories: the dev workspace (since 2026-06-07), and the pre-move
# dev URL and staging repo under the personal account. Assembled at runtime for
# the same reason as the pattern above. The public repo's own name,
# chaoscypherinc/chaoscypher, matches neither.
_PRODUCT = "chaoscypher"
PRIVATE_REPO_MARKERS = (f"{_PRODUCT}-dev", f"dmacpherson/{_PRODUCT}")

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
    tracked: frozenset[str] | None = None,
) -> list[str]:
    """Return ``path:lineno: line`` strings for every private-docs reference found.

    ``tracked`` (repo-relative POSIX paths) limits the scan to those files: in a
    checkout, untracked build output such as the docs site's ``.docusaurus/``
    cache holds absolute paths, which name the checkout's own directory.
    """
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

    if tracked is not None:
        paths_to_scan = [p for p in paths_to_scan if p.relative_to(repo_root).as_posix() in tracked]

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
            if _PATTERN.search(line) or any(m in line for m in PRIVATE_REPO_MARKERS):
                hits.append(f"{path.relative_to(repo_root)}:{lineno}: {line.strip()}")
    return hits


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=repo_root, capture_output=True, text=True, check=True
    )
    hits = find_internal_refs(repo_root, tracked=frozenset(listing.stdout.split("\0")) - {""})
    if hits:
        print("Shipped files reference a private docs path or repo (not in the public export):")
        for hit in hits:
            print(f"  {hit}")
        print("\nReplace with a self-contained summary or a public-docs link.")
        return 1

    print("OK: no private docs references in shipped trees.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
