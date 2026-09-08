#!/usr/bin/env python3
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Print the changelog section for a release tag as GitHub Release notes.

The canonical changelog is the docs page ``packages/docs/docs/about/changelog.md``,
where every release is a level-4 heading such as ``#### v0.4.1 (2026-08-25)``
followed by level-5 subsections. The tag-triggered ``release.yml`` workflow
calls this script to turn that section into the body of the GitHub Release.

Usage:
    python scripts/changelog_section.py v0.4.1
    python scripts/changelog_section.py v0.4.1 --changelog path/to/changelog.md

Exit status 1 when the version has no section, so the caller can fall back to
a generic note instead of publishing an empty body.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


CHANGELOG_URL = "https://chaoscypher.com/about/changelog"
DEFAULT_CHANGELOG = Path("packages/docs/docs/about/changelog.md")

# A release section starts at a level-4 heading naming the version and ends at
# the next heading of level 4 or shallower (the next release or the next month).
_SECTION_END = re.compile(r"^#{1,4}\s")


def _heading_pattern(version: str) -> re.Pattern[str]:
    """Match the ``#### vX.Y.Z`` heading for ``version`` (with or without ``v``)."""
    bare = version[1:] if version.startswith("v") else version
    return re.compile(rf"^####\s+v?{re.escape(bare)}(?:\s|$)")


def extract_section(text: str, version: str) -> str | None:
    """Return the body of ``version``'s section, or ``None`` when absent.

    The heading line itself is excluded; leading and trailing blank lines are
    trimmed so the result drops straight into a release body.
    """
    heading = _heading_pattern(version)
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        return None
    body: list[str] = []
    for line in lines[start + 1 :]:
        if _SECTION_END.match(line):
            break
        body.append(line.rstrip())
    return "\n".join(body).strip("\n")


def release_notes(text: str, version: str) -> str | None:
    """Return the release body for ``version``: its section plus the changelog link."""
    section = extract_section(text, version)
    if section is None:
        return None
    return f"{section}\n\nFull changelog: {CHANGELOG_URL}\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: print the notes for one tag, exit 1 when missing."""
    parser = argparse.ArgumentParser(description="Print a release's changelog section.")
    parser.add_argument("tag", help="Release tag or version, e.g. v0.4.1")
    parser.add_argument(
        "--changelog",
        type=Path,
        default=DEFAULT_CHANGELOG,
        help=f"Changelog markdown file (default: {DEFAULT_CHANGELOG})",
    )
    args = parser.parse_args(argv)

    notes = release_notes(args.changelog.read_text(encoding="utf-8"), args.tag)
    if notes is None:
        print(f"no changelog section for {args.tag} in {args.changelog}", file=sys.stderr)
        return 1
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
