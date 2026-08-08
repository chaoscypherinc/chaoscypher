# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the actionability-aware npm audit gate (scripts/npm_audit_gate.py).

The gate exists so an upstream advisory with **no published fix** cannot halt
every push, while anything with a bump available still blocks. The load-bearing
behaviour is the split, so these tests pin both directions — a gate that only
ever passes would be worse than no gate at all.

The propagation case is the subtle one: npm emits an entry per affected
package, and a downstream entry can claim ``fixAvailable: true`` while the root
advisory it inherits has no fix (observed 2026-08-07, where npm reported fixes
for 8 ``@docusaurus/*`` packages whose only advisory came from ``image-size``
and ``npm audit fix`` cleared none). Classifying those would block on something
nothing can clear, so only advisory-carrying roots are classified.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _load_gate():
    """Import scripts/npm_audit_gate.py as a module."""
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "npm_audit_gate.py"
    spec = importlib.util.spec_from_file_location("npm_audit_gate", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _root(severity: str, fix: Any, title: str = "boom") -> dict:
    """Build a vulnerability entry that carries its own advisory."""
    return {
        "severity": severity,
        "range": "*",
        "via": [{"title": title, "url": f"https://github.com/advisories/{title}"}],
        "fixAvailable": fix,
    }


def _downstream(severity: str, fix: Any, root_name: str) -> dict:
    """Build an entry that merely depends on another vulnerable package."""
    return {"severity": severity, "range": "*", "via": [root_name], "fixAvailable": fix}


def test_unfixable_root_is_reported_but_not_blocking() -> None:
    """A high root with no published fix must not block."""
    gate = _load_gate()
    report = {"vulnerabilities": {"image-size": _root("high", False)}}

    fixable, unfixable, propagated = gate.classify(report, "high")

    assert fixable == []
    assert [r["name"] for r in unfixable] == ["image-size"]
    assert propagated == 0


def test_fixable_root_still_blocks() -> None:
    """The gate must keep failing when a bump is available — otherwise it is decorative."""
    gate = _load_gate()
    report = {
        "vulnerabilities": {
            "nanoid": _root(
                "high", {"name": "nanoid", "version": "5.1.16", "isSemVerMajor": False}
            ),
        }
    }

    fixable, unfixable, _ = gate.classify(report, "high")

    assert [r["name"] for r in fixable] == ["nanoid"]
    assert unfixable == []


def test_downstream_entries_do_not_block_when_root_is_unfixable() -> None:
    """Regression: npm marks downstream packages fixable even when the root is not.

    Without this rule the 2026-08-07 image-size advisory blocked every push via
    8 ``@docusaurus/*`` entries that no bump could clear.
    """
    gate = _load_gate()
    report = {
        "vulnerabilities": {
            "image-size": _root("high", False),
            "@docusaurus/theme-classic": _downstream("high", True, "image-size"),
            "@docusaurus/plugin-sitemap": _downstream("high", True, "image-size"),
        }
    }

    fixable, unfixable, propagated = gate.classify(report, "high")

    assert fixable == []
    assert [r["name"] for r in unfixable] == ["image-size"]
    assert propagated == 2


def test_a_fixable_root_blocks_even_alongside_an_unfixable_one() -> None:
    """One unfixable finding must not mask a second, actionable one."""
    gate = _load_gate()
    report = {
        "vulnerabilities": {
            "image-size": _root("high", False),
            "nanoid": _root("high", True),
        }
    }

    fixable, unfixable, _ = gate.classify(report, "high")

    assert [r["name"] for r in fixable] == ["nanoid"]
    assert [r["name"] for r in unfixable] == ["image-size"]


def test_severity_floor_excludes_lower_severities() -> None:
    """Moderate findings are below the default floor and are ignored entirely."""
    gate = _load_gate()
    report = {"vulnerabilities": {"dompurify": _root("moderate", True)}}

    fixable, unfixable, propagated = gate.classify(report, "high")

    assert (fixable, unfixable, propagated) == ([], [], 0)


def test_unknown_severity_is_treated_as_blocking() -> None:
    """An unrecognised severity string must fail closed, not silently pass."""
    gate = _load_gate()
    report = {"vulnerabilities": {"mystery": _root("apocalyptic", True)}}

    fixable, _, _ = gate.classify(report, "high")

    assert [r["name"] for r in fixable] == ["mystery"]
