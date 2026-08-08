#!/usr/bin/env python3
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Fail if a cc-metrics-collector artifact is malformed.

`internal/metrics/` is written wholesale by the `cc-metrics-collector`
routine each run and reaches `main` via its Friday consolidation PR, which
the collector auto-merges under `auto-merge:deps`' sibling class
`auto-merge:data`. Nothing else reads these files until a later run does, so
a malformed write is invisible until it breaks something downstream.

Four defects of this shape landed in ten days (2026-07-23 duplicate
`collection_gaps` key; 2026-07-24 `PLACEHOLDER_SESSION_USAGE`; 2026-07-31
`PLACEHOLDER_USAGE`, which would have overwritten the only live copy of the
real value had it merged; 2026-08-02 a trailing blank line in
`scoreboard.md` that made pre-commit's `end-of-file-fixer` abort **every**
push from **every** branch, always after the full ~13-minute CI sweep had
already passed). Each was caught only by a human or a later run happening to
read the file.

The collector's own dashboard argued for a mechanical guard rather than more
care at the write step, and flagged it for the retro rather than
self-prescribing. This is that guard, wired into the `lint-internal-refs` CI
step so the routines' own sweep enforces it — pre-commit alone is not enough,
because the cloud sandbox runs `scripts/run_ci.py` directly and never invokes
pre-commit.

No-ops when `internal/` is absent: the private tree is stripped from the
public export, where this script still ships and runs.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml


_METRICS_DIR = Path("internal/metrics")
_CHECKED_SUFFIXES = (".md", ".yaml", ".yml")

# The collector stamps guardrail-10 self-metering into these files. When a run
# writes the literal placeholder instead of substituting the real value, the
# figure is lost silently -- and step 1 of the next run hard-resets the branch,
# destroying the last good copy.
#
# Match the placeholder only in *value* position, never a prose mention: these
# same files legitimately narrate the past incidents (e.g. scoreboard.md's
# collection-gaps entry naming PLACEHOLDER_SESSION_USAGE, and the `# SELF-HEALED`
# trailing comments on two real session_usage values). Flagging those would make
# the guard cry wolf on the very record of the defect it exists to prevent.
_PLACEHOLDER_PATTERNS = (
    # A YAML/front-matter key whose value starts with the placeholder --
    # the exact 2026-07-31 shape: `session_usage: PLACEHOLDER_USAGE`.
    re.compile(r"^\s*[\w-]+\s*:\s*[\"']?PLACEHOLDER", re.MULTILINE),
    # A markdown stamp line that is nothing but the placeholder token.
    re.compile(r"^\s*[\"'`]?PLACEHOLDER[\w-]*[\"'`]?\s*$", re.MULTILINE),
)


class _DuplicateKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently
    keeping the last one (the 2026-07-23 `collection_gaps` defect)."""


def _no_duplicate_keys(loader: _DuplicateKeyLoader, node: Any) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            mark = key_node.start_mark
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"duplicate key {key!r}",
                mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys
)


def _check_file(path: Path) -> list[str]:
    """Return a list of human-readable problems found in one artifact."""
    problems: list[str] = []
    raw = path.read_bytes()
    if not raw:
        return [f"{path}: file is empty"]

    text = raw.decode("utf-8")

    for pattern in _PLACEHOLDER_PATTERNS:
        for match in pattern.finditer(text):
            lineno = text.count("\n", 0, match.start()) + 1
            line = text.splitlines()[lineno - 1]
            problems.append(
                f"{path}:{lineno}: unsubstituted placeholder in value position "
                f"-- the run wrote its template instead of the real value: "
                f"{line.strip()[:100]}"
            )

    # Exactly one trailing newline. Two (a trailing blank line) makes
    # pre-commit's end-of-file-fixer rewrite the file and abort the push.
    if not text.endswith("\n"):
        problems.append(f"{path}: missing trailing newline")
    elif text.endswith("\n\n"):
        problems.append(
            f"{path}: trailing blank line -- end-of-file-fixer will rewrite "
            f"this and abort any push from any branch"
        )

    if path.suffix in (".yaml", ".yml"):
        try:
            # _DuplicateKeyLoader subclasses SafeLoader, so `!!python/object`
            # tags are still rejected -- this is as safe as `safe_load`, which
            # cannot be used here because it silently keeps the last of a
            # duplicated key rather than reporting it.
            yaml.load(text, Loader=_DuplicateKeyLoader)
        except yaml.YAMLError as exc:
            problems.append(f"{path}: does not parse -- {exc}")

    return problems


def main() -> int:
    if not _METRICS_DIR.is_dir():
        # Public export: internal/ is stripped. Nothing to check.
        return 0

    problems: list[str] = []
    for path in sorted(_METRICS_DIR.rglob("*")):
        if path.is_file() and path.suffix in _CHECKED_SUFFIXES:
            problems.extend(_check_file(path))

    if problems:
        print("Malformed cc-metrics-collector artifact(s):\n", flush=True)
        for problem in problems:
            print(f"  {problem}", flush=True)
        print(
            "\nThese files are written wholesale by the collector; fix the run "
            "that produced them, not just the file.",
            flush=True,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
