# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The Makefile lint-claude banner must not hardcode CC-rule IDs.

Hardcoded rule lists in echo banners silently drift as rules are added
(the May/June 2026 census moved from 32 to 39 rules and the banner kept
printing the old list). The banner must describe each stage generically
and point at the source of truth instead of enumerating IDs.
"""

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[5]
MAKEFILE = REPO_ROOT / "Makefile"


def _recipe_lines(target: str) -> list[str]:
    """Return the recipe lines (tab-indented body) of a Makefile target."""
    text = MAKEFILE.read_text(encoding="utf-8")
    lines = text.splitlines()
    body: list[str] = []
    in_target = False
    for line in lines:
        if re.match(rf"^{re.escape(target)}\s*:", line):
            in_target = True
            continue
        if in_target:
            if line.startswith(("\t", "#")) or not line.strip():
                body.append(line)
                if not line.startswith("\t") and line.strip():
                    continue
            else:
                break
    return body


@pytest.mark.unit
@pytest.mark.core
class TestLintClaudeBanner:
    """lint-claude echo banner stays generic so it cannot drift."""

    def test_makefile_exists(self):
        """Sanity: the repo-root Makefile is where we expect it."""
        if not MAKEFILE.exists():
            pytest.skip("Makefile not present in this checkout")
        assert MAKEFILE.is_file()

    def test_banner_does_not_enumerate_rule_ids(self):
        """No CC0xx IDs inside the lint-claude target's echo lines."""
        if not MAKEFILE.exists():
            pytest.skip("Makefile not present in this checkout")
        echo_lines = [ln for ln in _recipe_lines("lint-claude") if "@echo" in ln]
        assert echo_lines, "lint-claude target has no echo banner lines"
        offenders = [ln for ln in echo_lines if re.search(r"CC0\d+", ln)]
        assert not offenders, f"Hardcoded rule IDs in lint-claude banner: {offenders}"
