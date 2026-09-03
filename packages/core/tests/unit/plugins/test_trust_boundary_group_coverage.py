# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Every code-executing entry-point group is documented in TRUST_BOUNDARY.md.

SECURITY.md designates ``plugins/TRUST_BOUNDARY.md`` as the authoritative
plugin-trust posture document, and that document asserts its group table is
complete. Historically two groups (``chaoscypher.edition``,
``chaoscypher.extensions``) executed code while absent from the table, so
operators audited an incomplete code-execution surface. This guard scans the
source trees for ``"chaoscypher.<group>"`` literals and fails when one is
missing from the document.
"""

from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[5]
_TRUST_BOUNDARY = (
    _REPO_ROOT / "packages" / "core" / "src" / "chaoscypher_core" / "plugins" / "TRUST_BOUNDARY.md"
)
_SRC_TREES = [_REPO_ROOT / "packages" / pkg / "src" for pkg in ("core", "cortex", "neuron", "cli")]
# Dotted "chaoscypher.*" strings that are not entry-point groups.
_NON_GROUP_LITERALS = {"chaoscypher.fish"}  # shell-completion filename


def test_all_entry_point_groups_documented() -> None:
    pattern = re.compile(r"[\"'](chaoscypher\.[a-z_]+)[\"']")
    found: set[str] = set()
    for tree in _SRC_TREES:
        assert tree.is_dir(), f"scan root missing (parents[] index drift?): {tree}"
        for path in tree.rglob("*.py"):
            for match in pattern.findall(path.read_text(encoding="utf-8")):
                if match not in _NON_GROUP_LITERALS:
                    found.add(match)

    assert found, "no entry-point group literals found — scan pattern broken?"
    doc = _TRUST_BOUNDARY.read_text(encoding="utf-8")
    missing = sorted(g for g in found if g not in doc)
    assert not missing, (
        f"entry-point group(s) {missing} execute code but are missing from "
        f"{_TRUST_BOUNDARY} — update its group table"
    )
