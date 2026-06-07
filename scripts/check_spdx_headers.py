#!/usr/bin/env python3
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Ensure every shipped source file carries an SPDX license header.

The project convention is ``SPDX-License-Identifier: AGPL-3.0-only`` on all
shipped source. This was applied by hand and drifted (Alembic migrations and
some frontend files shipped without it). Run as a pre-commit hook and in the
local CI sweep; ``--fix`` inserts the missing header.

Not a license-validity check (the root LICENSE + per-package metadata cover the
tree) — purely a consistency guard so the convention is mechanically enforced.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


_SPDX = "SPDX-License-Identifier: AGPL-3.0-only"
_COPYRIGHT = "Copyright (C) 2024-2026 Chaos Cypher, Inc."
_HEAD_LINES = 5

# (root, glob, comment-prefix). Generated + vendored code is excluded.
# scripts/, tools/, and e2e/ ship in the public export, so they are held to
# the same header convention as packages/*/src.
_TARGETS = (
    ("packages/core/src", "**/*.py", "# "),
    ("packages/cortex/src", "**/*.py", "# "),
    ("packages/neuron/src", "**/*.py", "# "),
    ("packages/cli/src", "**/*.py", "# "),
    ("packages/interface/src", "**/*.ts", "// "),
    ("packages/interface/src", "**/*.tsx", "// "),
    ("scripts", "**/*.py", "# "),
    ("tools", "**/*.py", "# "),
    ("e2e", "**/*.py", "# "),
)
_SKIP_PARTS = {"node_modules", "__pycache__", "generated"}
# semgrep rule fixtures are deliberately header-free: their ``ruleid:``
# annotations assert on specific line numbers, which inserting header lines
# would shift and silently break the rule self-tests.
_SKIP_TREES = ("tools/semgrep/tests",)


def _iter_files(repo_root: Path):
    for root, pattern, prefix in _TARGETS:
        base = repo_root / root
        if not base.exists():
            continue
        for path in base.glob(pattern):
            if path.is_dir() or any(part in _SKIP_PARTS for part in path.parts):
                continue
            if path.name.endswith(".d.ts"):
                continue
            rel = path.relative_to(repo_root).as_posix()
            if any(rel.startswith(f"{tree}/") for tree in _SKIP_TREES):
                continue
            yield path, prefix


def _has_spdx(text: str) -> bool:
    return any(_SPDX in line for line in text.splitlines()[:_HEAD_LINES])


def _insert_header(text: str, prefix: str) -> str:
    lines = text.splitlines(keepends=True)
    head = "".join(lines[:_HEAD_LINES])
    new_header = []
    if _COPYRIGHT not in head:
        new_header.append(f"{prefix}{_COPYRIGHT}\n")
    new_header.append(f"{prefix}{_SPDX}\n")
    # Keep a shebang on the very first line.
    if lines and lines[0].startswith("#!"):
        return lines[0] + "".join(new_header) + "".join(lines[1:])
    return "".join(new_header) + "".join(lines)


def _iter_missing(repo_root: Path):
    """Yield ``(path, comment_prefix)`` for every shipped file missing the header."""
    for path, prefix in _iter_files(repo_root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # fmt: skip
            continue
        if not _has_spdx(text):
            yield path, prefix, text


def find_missing(repo_root: Path) -> list[Path]:
    """Public check surface: shipped files missing the SPDX header."""
    return [path for path, _prefix, _text in _iter_missing(repo_root)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="Insert the missing SPDX header.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    missing: list[Path] = []
    for path, prefix, text in _iter_missing(repo_root):
        missing.append(path)
        if args.fix:
            path.write_text(_insert_header(text, prefix), encoding="utf-8")

    if missing and not args.fix:
        print(f"Missing SPDX header on {len(missing)} shipped file(s):")
        for path in missing:
            print(f"  {path.relative_to(repo_root)}")
        print("\nRun: uv run python scripts/check_spdx_headers.py --fix")
        return 1
    if missing:
        print(f"Inserted SPDX header into {len(missing)} file(s).")
        return 0
    print("OK: all shipped source files carry an SPDX header.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
