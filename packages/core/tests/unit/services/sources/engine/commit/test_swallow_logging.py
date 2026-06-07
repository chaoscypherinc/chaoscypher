# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Stopgap smoke test: every swallow-style 'except Exception:' block in the
commit pipeline emits a unique structured event.

Audit fix #M1 (stopgap). A deeper per-block audit (which swallows are correct
vs which should propagate) is tracked as a follow-up plan.

Approach chosen: lint-style text scan rather than per-block behaviour tests.
Wiring up isolated mocks for 12 swallow sites across 4 files would require
a 200+ line fixture setup that duplicates the real integration tests.  Instead
this module:

  1. Reads all four commit files as source text.
  2. Finds every ``except Exception`` clause.
  3. Excludes blocks that terminate with a bare ``raise`` (re-raisers, not
     swallowers).
  4. For every remaining (swallow) block: asserts that within 15 lines there
     is a ``logger.exception(`` call whose first argument is a unique,
     descriptive literal string (not an f-string — CC022).

This pins the M1 contract without duplicating integration logic.
"""

from __future__ import annotations

import re
from pathlib import Path


# Absolute paths to the four files under test.
_COMMIT_DIR = (
    Path(__file__).parents[6]
    / "src"
    / "chaoscypher_core"
    / "services"
    / "sources"
    / "engine"
    / "commit"
)

_FILES = [
    _COMMIT_DIR / "service.py",
    _COMMIT_DIR / "relation.py",
    _COMMIT_DIR / "entity.py",
    _COMMIT_DIR / "template.py",
]

_EXCEPT_EXCEPTION_RE = re.compile(r"^\s+(except Exception)")
_LOGGER_EXCEPTION_WITH_LITERAL_RE = re.compile(r"""logger\.exception\(\s*["']([^"']+)["']""")
_BARE_RAISE_RE = re.compile(r"^\s+raise\s*$")


def _collect_swallow_blocks(
    source: str,
) -> list[tuple[int, list[str]]]:
    """Return (lineno, block_lines) for each swallow 'except Exception:' clause.

    A block is the except clause plus up to 15 following lines at the same or
    deeper indentation.  Blocks that contain a bare ``raise`` statement at the
    end (re-raisers) are excluded — they are not swallows.
    """
    lines = source.splitlines()
    blocks: list[tuple[int, list[str]]] = []

    for i, line in enumerate(lines):
        if not _EXCEPT_EXCEPTION_RE.match(line):
            continue

        lineno = i + 1  # 1-based
        indent = len(line) - len(line.lstrip())

        # Collect up to 15 lines of the block body (lines that are more indented)
        block_lines: list[str] = [line]
        for j in range(i + 1, min(i + 16, len(lines))):
            next_line = lines[j]
            stripped = next_line.strip()
            if not stripped:
                block_lines.append(next_line)
                continue
            next_indent = len(next_line) - len(next_line.lstrip())
            if next_indent <= indent:
                break  # back to same or outer level
            block_lines.append(next_line)

        # Skip re-raisers: block body ends with a bare ``raise``
        non_empty_body = [line for line in block_lines[1:] if line.strip()]
        if non_empty_body and _BARE_RAISE_RE.match(non_empty_body[-1]):
            continue  # re-raiser, not a swallow

        blocks.append((lineno, block_lines))

    return blocks


def test_every_swallow_has_logger_exception_event():
    """Every swallow 'except Exception:' in the four commit files has a
    logger.exception call with a literal event name within the block.
    """
    failures: list[str] = []

    for filepath in _FILES:
        assert filepath.exists(), f"Commit file missing: {filepath}"
        source = filepath.read_text(encoding="utf-8")
        blocks = _collect_swallow_blocks(source)

        for lineno, block_lines in blocks:
            block_text = "\n".join(block_lines)
            m = _LOGGER_EXCEPTION_WITH_LITERAL_RE.search(block_text)
            if m is None:
                failures.append(
                    f"{filepath.name}:{lineno} — swallow 'except Exception:' has no "
                    f"logger.exception(<literal>) within the block"
                )

    assert not failures, "\n".join(failures)


def test_no_duplicate_event_names_across_commit_files():
    """Every logger.exception event name in the four commit files is unique.

    Duplicate names defeat the per-swallow monitoring contract: operators
    cannot distinguish which block fired if two swallows share a name.
    """
    event_names: list[str] = []

    for filepath in _FILES:
        source = filepath.read_text(encoding="utf-8")
        for m in re.finditer(r"""logger\.exception\(\s*["']([^"']+)["']""", source):
            event_names.append(m.group(1))

    seen: dict[str, int] = {}
    for name in event_names:
        seen[name] = seen.get(name, 0) + 1
    duplicates = [f"'{n}' appears {c} times" for n, c in seen.items() if c > 1]

    assert not duplicates, "Duplicate logger.exception event names:\n" + "\n".join(duplicates)


def test_no_event_name_is_fstring():
    """No logger.exception call in commit files uses an f-string as event name (CC022)."""
    violations: list[str] = []

    for filepath in _FILES:
        source = filepath.read_text(encoding="utf-8")
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if re.search(r"logger\.exception\(\s*f[\"']", line):
                violations.append(f"{filepath.name}:{i + 1} — f-string event name: {line.strip()}")

    assert not violations, "CC022 violations in commit files:\n" + "\n".join(violations)
