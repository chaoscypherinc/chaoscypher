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


def test_finds_every_private_repo_name(tmp_path: Path) -> None:
    """A private repo name in a docstring or a workflow is caught at commit time (#808).

    Until 2026-10-17 only the weekly export gate looked for these, so a test docstring naming
    the dev repo passed every commit and PR check and blocked that week's public snapshot.
    """
    pkg = tmp_path / "packages" / "core"
    pkg.mkdir(parents=True)
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    for i, marker in enumerate(_GUARD.PRIVATE_REPO_MARKERS):
        (pkg / f"t{i}.py").write_text(f'"""Found on {marker} in October."""\n', encoding="utf-8")
        (workflows / f"w{i}.yml").write_text(
            f"if: github.repository == 'org/{marker}'\n", encoding="utf-8"
        )
    hits = _GUARD.find_internal_refs(tmp_path)
    for i in range(len(_GUARD.PRIVATE_REPO_MARKERS)):
        assert any(f"t{i}.py" in h for h in hits)
        assert any(f"w{i}.yml" in h for h in hits)


def test_the_public_repo_name_is_not_private(tmp_path: Path) -> None:
    pkg = tmp_path / "packages" / "core"
    pkg.mkdir(parents=True)
    public = "/".join(("chaoscypherinc", "chaoscypher"))
    (pkg / "thing.py").write_text(f"# see https://github.com/{public}/issues\n", encoding="utf-8")
    assert _GUARD.find_internal_refs(tmp_path) == []


def test_tracked_limits_the_scan_to_tracked_files(tmp_path: Path) -> None:
    """Untracked build output is not shipped, and in a checkout it names the checkout's path."""
    docs = tmp_path / "packages" / "docs"
    (docs / ".docusaurus").mkdir(parents=True)
    marker = _GUARD.PRIVATE_REPO_MARKERS[0]
    (docs / ".docusaurus" / "client-modules.js").write_text(
        f'require("/home/me/{marker}/packages/docs/x.css");\n', encoding="utf-8"
    )
    (docs / "page.md").write_text(f"see {_PRIVATE_REF}\n", encoding="utf-8")
    hits = _GUARD.find_internal_refs(tmp_path, tracked=frozenset({"packages/docs/page.md"}))
    assert len(hits) == 1
    assert "page.md" in hits[0]
