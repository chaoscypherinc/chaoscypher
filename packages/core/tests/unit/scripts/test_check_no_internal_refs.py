# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the public-export internal-refs guard (scripts/check_no_internal_refs.py).

The guard fails if any *shipped* file references a private ``internal/<subdir>/``
path, which would become a dangling pointer (and leak private dir names) in the
public export. These tests pin behavior: it catches refs across the full shipped
set, ignores unreadable files instead of crashing, and passes on a clean tree.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_guard():
    """Import scripts/check_no_internal_refs.py as a module."""
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "check_no_internal_refs.py"
    spec = importlib.util.spec_from_file_location("check_no_internal_refs", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GUARD = _load_guard()

# A reference that MUST trip the guard (private subdir under internal/).
# Assembled at runtime so this test file does not itself contain the forbidden
# literal (the guard scans packages/, so a verbatim string would trip it on the
# guard's own test — mirrors the runtime-assembly trick in the guard's source).
_PRIVATE_REF = "/".join(("internal", "plans", "secret-thing.md"))
_BAD = f"see {_PRIVATE_REF} for details"
# A reference that must NOT trip it (internal/ but not a private subdir + slash).
_OK = "the internal API of the module is documented here"


def test_finds_ref_in_packages(tmp_path: Path) -> None:
    pkg = tmp_path / "packages" / "core"
    pkg.mkdir(parents=True)
    (pkg / "thing.py").write_text(f"# {_BAD}\n", encoding="utf-8")
    hits = _GUARD.find_internal_refs(tmp_path)
    assert any("thing.py" in h for h in hits)


def test_clean_tree_passes(tmp_path: Path) -> None:
    pkg = tmp_path / "packages" / "core"
    pkg.mkdir(parents=True)
    (pkg / "thing.py").write_text(f"# {_OK}\n", encoding="utf-8")
    assert _GUARD.find_internal_refs(tmp_path) == []


def test_skips_unreadable_file(tmp_path: Path) -> None:
    # A non-UTF-8 blob must be skipped (exercises the except branch), not crash.
    pkg = tmp_path / "packages" / "core"
    pkg.mkdir(parents=True)
    blob = b"\xff\xfe\x00\x01 " + _PRIVATE_REF.encode("ascii")
    (pkg / "blob.md").write_bytes(blob)
    assert _GUARD.find_internal_refs(tmp_path) == []


def test_finds_ref_in_root_file(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text(f"\t# {_BAD}\n", encoding="utf-8")
    hits = _GUARD.find_internal_refs(tmp_path)
    assert any("Makefile" in h for h in hits)


def test_finds_ref_in_tools_and_github(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "policy.yml").write_text(f"# {_BAD}\n", encoding="utf-8")
    gh = tmp_path / ".github" / "workflows"
    gh.mkdir(parents=True)
    (gh / "ci.yml").write_text(f"# {_BAD}\n", encoding="utf-8")
    hits = _GUARD.find_internal_refs(tmp_path)
    assert any("policy.yml" in h for h in hits)
    assert any("ci.yml" in h for h in hits)


def test_does_not_scan_internal_tree(tmp_path: Path) -> None:
    # internal/ itself is never shipped, so refs inside it must be ignored.
    internal = tmp_path / "internal" / "plans"
    internal.mkdir(parents=True)
    (internal / "x.md").write_text(f"# {_BAD}\n", encoding="utf-8")
    assert _GUARD.find_internal_refs(tmp_path) == []
