#!/usr/bin/env python3
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify a release tag matches the version in every package's pyproject.toml.

Used by the release workflows: a ``vX.Y.Z`` GitHub Release must correspond to
``version = "X.Y.Z"`` in all four publishable packages, so the published image
(``ghcr.io/...:X.Y.Z``) and the PyPI packages (``chaoscypher-*==X.Y.Z``) share
one coherent version. Run before building/publishing; a mismatch fails fast.

Usage:
    python scripts/check_release_version.py v0.1.2
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path


# Packages that ship to PyPI and whose version must match the release tag.
PACKAGES = ("core", "cortex", "neuron", "cli")


def tag_to_version(tag: str) -> str:
    """Strip a leading ``v`` from a release tag (``v0.1.2`` -> ``0.1.2``)."""
    return tag[1:] if tag.startswith("v") else tag


def find_mismatches(tag: str, root: Path) -> list[str]:
    """Return one message per package whose version differs from the tag."""
    expected = tag_to_version(tag)
    mismatches: list[str] = []
    for pkg in PACKAGES:
        pyproject = root / "packages" / pkg / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        version = data["project"]["version"]
        if version != expected:
            mismatches.append(f"  packages/{pkg}: {version} (tag wants {expected})")
    return mismatches


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_release_version.py <tag>", file=sys.stderr)
        return 2

    tag = argv[1]
    root = Path(__file__).resolve().parent.parent
    mismatches = find_mismatches(tag, root)

    if mismatches:
        print(f"Release tag {tag!r} does not match package versions:", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        print(
            "Bump all four packages/*/pyproject.toml to the tagged version, or retag.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: tag {tag} matches all {len(PACKAGES)} package versions ({tag_to_version(tag)}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
